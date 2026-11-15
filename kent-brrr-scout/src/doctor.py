"""Setup checks, run from the UI ("Check setup") or `python -m src.doctor`."""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

import httpx

from .config import load_config, load_dotenv


def _check(name: str, ok: bool | None, detail: str, fix: str = "") -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail, "fix": fix}


def run_checks(include_portals: bool = True) -> list[dict[str, Any]]:
    load_dotenv()
    cfg = load_config()
    out: list[dict[str, Any]] = []
    v = sys.version_info
    out.append(_check("Python", v >= (3, 12), f"{v.major}.{v.minor}.{v.micro}",
                      "Install Python 3.12 or newer from python.org"))

    has_pw = importlib.util.find_spec("playwright") is not None
    out.append(_check("Playwright", has_pw, "installed" if has_pw else "missing",
                      "Run the start script again, or `pip install playwright`"))

    if has_pw and include_portals:
        from .portals.browser import PortalBrowser

        try:
            with PortalBrowser(cfg) as br:
                out.append(_check("Browser", True, "started OK"))
                for name in cfg["portals"].get("enabled", []):
                    url = cfg["portals"]["base_urls"].get(name)
                    if not url:
                        continue
                    res = br.fetch(url, cards=False, polite=True)
                    if res.blocked:
                        out.append(_check(f"Portal: {name}", False, f"blocked ({res.blocked})",
                                          "Try 'show browser window' in Settings, or disable this portal"))
                    elif res.error:
                        out.append(_check(f"Portal: {name}", False, res.error[:200], "Check your internet"))
                    elif res.status and res.status >= 400:
                        out.append(_check(f"Portal: {name}", False, f"HTTP {res.status}",
                                          "The site returned an error — try again later"))
                    else:
                        out.append(_check(f"Portal: {name}", True, f"reachable (HTTP {res.status})"))
        except Exception as exc:  # noqa: BLE001
            out.append(_check("Browser", False, str(exc)[:300],
                              "Install Google Chrome, or run `python -m playwright install chromium`"))

    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        try:
            import anthropic

            anthropic.Anthropic().models.retrieve(cfg.get("claude", {}).get("model", "claude-opus-5-5"))
            out.append(_check("Claude API", True, "key works"))
        except Exception as exc:  # noqa: BLE001
            out.append(_check("Claude API", False, str(exc)[:200], "Check ANTHROPIC_API_KEY in Settings"))
    else:
        out.append(_check("Claude API", None, "not set — refurb tier uses keyword heuristic",
                          "Optional: add ANTHROPIC_API_KEY in Settings"))

    out.append(_check("PropertyData", None if not os.environ.get("PROPERTYDATA_API_KEY") else True,
                      "key set" if os.environ.get("PROPERTYDATA_API_KEY")
                      else "not set — comps come from portal data + Land Registry",
                      "Optional paid comps source"))

    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            r = httpx.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=10)
            ok = r.status_code == 200 and r.json().get("ok")
            out.append(_check("Telegram", bool(ok), "bot token works" if ok else r.text[:150],
                              "Check the token from @BotFather"))
        except httpx.HTTPError as exc:
            out.append(_check("Telegram", False, str(exc)[:150]))
    else:
        out.append(_check("Telegram", None, "not set — alerts only show in the app",
                          "Optional: add bot token + chat id in Settings"))

    try:
        r = httpx.get(cfg["ingestion"]["land_registry_sparql"],
                      params={"query": "SELECT * WHERE {?s ?p ?o} LIMIT 1", "output": "json"}, timeout=20)
        out.append(_check("Land Registry", r.status_code == 200, f"HTTP {r.status_code}",
                          "Sold-price comps will be missing until this works"))
    except httpx.HTTPError as exc:
        out.append(_check("Land Registry", False, str(exc)[:150], "Check your internet"))

    from .scheduler import status

    st = status()
    out.append(_check("Daily schedule", st.get("installed", False),
                      f"{st.get('platform')}: daily at {st.get('time')}" if st.get("installed")
                      else "not installed", "Settings › Daily run › Install"))

    from .scheduler import app_status

    ap = app_status()
    if ap.get("supported"):
        out.append(_check("App at logon", ap.get("installed", False),
                          f"Windows task: {ap.get('state') or 'installed'}" if ap.get("installed")
                          else "not installed (the phone app only works while the launcher window is open)",
                          "python -m src.scheduler install-app"))
    return out


if __name__ == "__main__":
    for c in run_checks():
        mark = {True: "OK ", False: "!! ", None: "-- "}[c["ok"]]
        print(f"{mark}{c['name']}: {c['detail']}" + (f"   → {c['fix']}" if c["ok"] is False else ""))
