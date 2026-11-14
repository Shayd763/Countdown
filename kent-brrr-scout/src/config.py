"""Config loading (YAML + .env) with per-outcode overrides."""

from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> dict[str, Any]:
    path = path or os.environ.get("BRRR_CONFIG") or str(PROJECT_ROOT / "config" / "postcodes.yaml")
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    with p.open() as fh:
        return yaml.safe_load(fh)


def all_outcodes(cfg: dict[str, Any]) -> list[str]:
    return [oc for group in cfg["outcodes"].values() for oc in group]


def config_for_outcode(cfg: dict[str, Any], outcode: str) -> dict[str, Any]:
    """Return the config with that outcode's overrides merged in."""
    override = (cfg.get("outcode_overrides") or {}).get(outcode.upper(), {})
    return _deep_merge(cfg, override) if override else cfg
