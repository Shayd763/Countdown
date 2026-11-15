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
    for line in path.read_text(encoding="utf-8").splitlines():
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


USER_OVERRIDES = PROJECT_ROOT / "config" / "user.yaml"  # written by the UI Settings page
ENV_PATH = PROJECT_ROOT / ".env"


@lru_cache(maxsize=4)
def _load_config_cached(path: str, user_mtime: float) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if user_mtime and USER_OVERRIDES.exists():
        cfg = _deep_merge(cfg, yaml.safe_load(USER_OVERRIDES.read_text(encoding="utf-8")) or {})
    return cfg


def load_config(path: str | None = None) -> dict[str, Any]:
    """Base YAML merged with config/user.yaml (UI-edited settings). Re-read when user.yaml changes."""
    path = path or os.environ.get("BRRR_CONFIG") or str(PROJECT_ROOT / "config" / "postcodes.yaml")
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    mtime = USER_OVERRIDES.stat().st_mtime if USER_OVERRIDES.exists() else 0.0
    return copy.deepcopy(_load_config_cached(str(p), mtime))


def save_user_overrides(updates: dict[str, Any]) -> dict[str, Any]:
    current = (yaml.safe_load(USER_OVERRIDES.read_text(encoding="utf-8")) or {}) if USER_OVERRIDES.exists() else {}
    merged = _deep_merge(current, updates)
    USER_OVERRIDES.write_text(
        "# Written by the Gatehouse Settings page. Overrides config/postcodes.yaml.\n"
        + yaml.safe_dump(merged, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return merged


SECRET_KEYS = [
    "ANTHROPIC_API_KEY", "PROPERTYDATA_API_KEY", "EPC_API_EMAIL", "EPC_API_TOKEN",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ALERT_WEBHOOK_URL",
]


def save_env(values: dict[str, str]) -> None:
    """Update .env in place (keeps unrelated lines) and the live process environment."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in remaining.items()]
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass
    for k, v in values.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def config_version(path: str | None = None) -> tuple[float, float]:
    """Changes whenever the base config or the Settings overrides change."""
    base = Path(path or os.environ.get("BRRR_CONFIG") or PROJECT_ROOT / "config" / "postcodes.yaml")
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    return (base.stat().st_mtime if base.exists() else 0.0,
            USER_OVERRIDES.stat().st_mtime if USER_OVERRIDES.exists() else 0.0)


def all_outcodes(cfg: dict[str, Any]) -> list[str]:
    return [oc for group in cfg["outcodes"].values() for oc in group]


def config_for_outcode(cfg: dict[str, Any], outcode: str) -> dict[str, Any]:
    """Return the config with that outcode's region overrides, then its own overrides, merged in."""
    from .geo import region_of

    out = cfg
    region_override = (cfg.get("region_overrides") or {}).get(region_of(outcode or ""), {})
    if region_override:
        out = _deep_merge(out, region_override)
    override = (cfg.get("outcode_overrides") or {}).get((outcode or "").upper(), {})
    return _deep_merge(out, override) if override else out
