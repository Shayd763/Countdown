"""Install / remove / inspect the daily morning run on this laptop.

    python -m src.scheduler install --time 07:30
    python -m src.scheduler status
    python -m src.scheduler remove

macOS  → a LaunchAgent (runs at the time; if the Mac was asleep, runs on wake)
Windows → a Task Scheduler task with "run as soon as possible after a missed start"
Linux  → a crontab line
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .config import PROJECT_ROOT

LABEL = "uk.kentbrrr.scout.daily"
WIN_TASK = "KentBRRRScoutDaily"
CRON_MARK = "# kent-brrr-scout daily"
LOG_DIR = PROJECT_ROOT / "data" / "logs"


def _python() -> str:
    exe = Path(sys.executable)
    if platform.system() == "Windows":
        pyw = exe.with_name("pythonw.exe")  # no console window popping up at 07:30
        if pyw.exists():
            return str(pyw)
    return str(exe)


def _parse_time(t: str) -> tuple[int, int]:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", t.strip())
    if not m:
        raise ValueError("time must be HH:MM (24h)")
    return int(m.group(1)), int(m.group(2))


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


# -- macOS ------------------------------------------------------------------

def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _mac_install(hour: int, minute: int) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [_python(), "-m", "src.main", "--daily"],
        "WorkingDirectory": str(PROJECT_ROOT),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(LOG_DIR / "schedule.out.log"),
        "StandardErrorPath": str(LOG_DIR / "schedule.err.log"),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"},
    }
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    _run(["launchctl", "bootout", f"gui/{uid}", str(path)])
    path.write_bytes(plistlib.dumps(plist))
    r = _run(["launchctl", "bootstrap", f"gui/{uid}", str(path)])
    if r.returncode != 0:
        r = _run(["launchctl", "load", "-w", str(path)])
        if r.returncode != 0:
            raise RuntimeError(f"launchctl failed: {r.stderr.strip()}")
    return f"LaunchAgent installed: daily at {hour:02d}:{minute:02d} ({path})"


def _mac_remove() -> str:
    path = _plist_path()
    _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)])
    if path.exists():
        path.unlink()
        return "LaunchAgent removed"
    return "No LaunchAgent was installed"


def _mac_status() -> dict[str, Any]:
    path = _plist_path()
    if not path.exists():
        return {"installed": False}
    data = plistlib.loads(path.read_bytes())
    sci = data.get("StartCalendarInterval", {})
    loaded = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"]).returncode == 0
    return {"installed": True, "loaded": loaded,
            "time": f"{sci.get('Hour', 0):02d}:{sci.get('Minute', 0):02d}", "where": str(path)}


# -- Windows ----------------------------------------------------------------

def _win_xml(hour: int, minute: int) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Gatehouse - daily property scan</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{hour:02d}:{minute:02d}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>
    <RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(_python())}</Command>
      <Arguments>-m src.main --daily</Arguments>
      <WorkingDirectory>{escape(str(PROJECT_ROOT))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""


def _win_install(hour: int, minute: int) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as fh:
        fh.write(_win_xml(hour, minute))
        xml_path = fh.name
    try:
        r = _run(["schtasks", "/Create", "/TN", WIN_TASK, "/XML", xml_path, "/F"])
    finally:
        os.unlink(xml_path)
    if r.returncode != 0:
        raise RuntimeError(f"schtasks failed: {r.stderr.strip() or r.stdout.strip()}")
    return f"Scheduled task '{WIN_TASK}' installed: daily at {hour:02d}:{minute:02d}"


def _win_remove() -> str:
    r = _run(["schtasks", "/Delete", "/TN", WIN_TASK, "/F"])
    return "Scheduled task removed" if r.returncode == 0 else "No scheduled task was installed"


def _win_status() -> dict[str, Any]:
    r = _run(["schtasks", "/Query", "/TN", WIN_TASK, "/FO", "LIST", "/V"])
    if r.returncode != 0:
        return {"installed": False}
    info = dict(re.findall(r"^([^:\r\n]+):\s+(.*)$", r.stdout, re.M))
    nxt = info.get("Next Run Time", "")
    return {"installed": True, "loaded": True, "time": info.get("Start Time", "")[:5],
            "next_run": nxt, "where": f"Task Scheduler › {WIN_TASK}"}


# -- Linux --------------------------------------------------------------------

def _cron_lines() -> list[str]:
    r = _run(["crontab", "-l"])
    return r.stdout.splitlines() if r.returncode == 0 else []


def _linux_install(hour: int, minute: int) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [ln for ln in _cron_lines() if CRON_MARK not in ln]
    lines.append(f'{minute} {hour} * * * cd "{PROJECT_ROOT}" && "{_python()}" -m src.main --daily '
                 f'>> "{LOG_DIR / "schedule.out.log"}" 2>&1 {CRON_MARK}')
    r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"crontab failed: {r.stderr.strip()}")
    return f"crontab entry installed: daily at {hour:02d}:{minute:02d}"


def _linux_remove() -> str:
    lines = _cron_lines()
    kept = [ln for ln in lines if CRON_MARK not in ln]
    if len(kept) == len(lines):
        return "No crontab entry was installed"
    subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True)
    return "crontab entry removed"


def _linux_status() -> dict[str, Any]:
    for ln in _cron_lines():
        if CRON_MARK in ln:
            minute, hour = ln.split()[:2]
            return {"installed": True, "loaded": True, "time": f"{int(hour):02d}:{int(minute):02d}",
                    "where": "crontab"}
    return {"installed": False}


# -- public -------------------------------------------------------------------

def install(time_str: str) -> str:
    hour, minute = _parse_time(time_str)
    system = platform.system()
    if system == "Darwin":
        return _mac_install(hour, minute)
    if system == "Windows":
        return _win_install(hour, minute)
    return _linux_install(hour, minute)


def remove() -> str:
    system = platform.system()
    return _mac_remove() if system == "Darwin" else _win_remove() if system == "Windows" else _linux_remove()


def status() -> dict[str, Any]:
    system = platform.system()
    try:
        st = (_mac_status() if system == "Darwin" else _win_status() if system == "Windows"
              else _linux_status())
    except FileNotFoundError as exc:  # launchctl / schtasks / crontab missing
        st = {"installed": False, "error": str(exc)}
    st["platform"] = {"Darwin": "macOS", "Windows": "Windows"}.get(system, system)
    return st


def main() -> None:
    ap = argparse.ArgumentParser(prog="brrr-schedule", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--time", default=None, help="HH:MM, default from config schedule.time")
    sub.add_parser("remove")
    sub.add_parser("status")
    args = ap.parse_args()
    if args.cmd == "install":
        from .config import load_config

        print(install(args.time or load_config().get("schedule", {}).get("time", "07:30")))
    elif args.cmd == "remove":
        print(remove())
    else:
        print(status())


if __name__ == "__main__":
    main()
