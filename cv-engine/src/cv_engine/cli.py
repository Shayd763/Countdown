"""Command line interface for the CV application engine.

Commands
--------
  init      Scaffold profile.yaml + config.yaml from the bundled examples.
  validate  Load and check the profile, printing any warnings.
  discover  Fetch + filter roles and print them (no CV generation).
  run       Full daily pipeline -> dated output folder + dashboard.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from .config import Config
from .discovery import discover
from .pipeline import run_pipeline
from .profile_store import ProfileError, load_profile, validate_profile

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent.parent  # cv-engine/
_DATA = _PKG_ROOT / "data"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def cmd_init(args: argparse.Namespace) -> int:
    targets = [
        (_DATA / "profile.example.yaml", Path(args.profile)),
        (_PKG_ROOT / "config.example.yaml", Path(args.config)),
    ]
    for src, dst in targets:
        if dst.exists() and not args.force:
            print(f"• {dst} already exists (use --force to overwrite)")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        print(f"✓ wrote {dst}")
    print("\nNext: edit your profile, then run `cv-engine run`.")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        profile = load_profile(args.profile)
    except ProfileError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    warnings = validate_profile(profile)
    print(f"✓ profile OK: {profile.contact.name} ({len(profile.experience)} roles, "
          f"{len(profile.skills)} skills)")
    for w in warnings:
        print(f"  ! {w}")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    profile = load_profile(args.profile)
    jobs = discover(config, profile)
    if not jobs:
        print("No matching roles found.")
        return 0
    print(f"{len(jobs)} matching role(s):\n")
    for j in jobs:
        print(f"  [{j.match_score:5.1f}] {j.title}  —  {j.company} ({j.location})")
        print(f"           IR35={j.ir35_status}  source={j.source}  {j.url}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    if args.output:
        config.output_dir = args.output
    profile = load_profile(args.profile)
    dashboard = run_pipeline(profile, config, run_date=args.date)
    print(f"\n✓ Dashboard: {dashboard}")
    print(f"  Open it in a browser to preview CVs, download PDFs and click apply.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cv-engine", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--profile", default="data/profile.yaml", help="path to profile YAML")
    parser.add_argument("--config", default="config.yaml", help="path to config YAML")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="scaffold profile.yaml + config.yaml")
    p_init.add_argument("--force", action="store_true", help="overwrite existing files")
    p_init.set_defaults(func=cmd_init)

    sub.add_parser("validate", help="validate the profile").set_defaults(func=cmd_validate)
    sub.add_parser("discover", help="find + print matching roles").set_defaults(func=cmd_discover)

    p_run = sub.add_parser("run", help="full daily pipeline")
    p_run.add_argument("--output", help="override output directory")
    p_run.add_argument("--date", help="override run date (YYYY-MM-DD)")
    p_run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
