"""Logging to console + data/logs/brrr.log (rotated) so scheduled runs leave a trail."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "brrr.log"


def _utf8_streams() -> None:
    """Windows falls back to cp1252 when output is redirected (scheduled task, pipe, service),
    and one '£' or '✅' then aborts the whole run. Always write UTF-8 and never crash on output.
    Under pythonw (scheduled tasks) there are no streams at all, which breaks uvicorn's logging
    setup, so give it a null device to write to (the log file still gets everything)."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 — lives for the process
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def setup_logging(verbose: bool = False) -> None:
    _utf8_streams()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(fh)
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def tail(lines: int = 200) -> str:
    if not LOG_FILE.exists():
        return ""
    with LOG_FILE.open(encoding="utf-8", errors="replace") as fh:
        return "".join(fh.readlines()[-lines:])
