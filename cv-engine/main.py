#!/usr/bin/env python3
"""Convenience entrypoint so the engine runs without installation.

    python main.py init
    python main.py run

It simply puts ``src`` on the path and delegates to the CLI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cv_engine.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
