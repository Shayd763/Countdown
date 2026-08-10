"""Configuration loading for the CV engine.

Config comes from a YAML file (``config.yaml`` by default) with environment
variable overrides for anything secret (API keys). Sensible defaults mean the
engine runs out-of-the-box against the bundled offline sample source with no
keys and no network access.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SEARCH_TERMS = [
    "inside IR35",
    "contract",
]

# The phrases the discovery layer looks for. Section order for the CV keeps the
# Harvard styling while leading with the most relevant content for an
# experienced contractor. Override in config.yaml if you prefer education-first.
DEFAULT_SECTION_ORDER = [
    "summary",
    "skills",
    "experience",
    "education",
    "certifications",
]


@dataclass
class Config:
    # Discovery
    sources: list[str] = field(default_factory=lambda: ["sample"])
    search_terms: list[str] = field(default_factory=lambda: list(DEFAULT_SEARCH_TERMS))
    locations: list[str] = field(default_factory=lambda: ["UK"])
    max_days_old: int = 3
    ir35_filter: str = "inside"          # inside | outside | any
    min_match_score: float = 0.0
    max_results: int = 50

    # Rendering / output
    section_order: list[str] = field(default_factory=lambda: list(DEFAULT_SECTION_ORDER))
    output_dir: str = "output"
    make_cover_note: bool = True

    # Tailoring
    use_llm: bool = False                # opt-in richer tailoring via Claude API
    llm_model: str = "claude-sonnet-5"

    # Secrets / API config (resolved from env at load time)
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    reed_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Config":
        data: dict[str, Any] = {}
        if path:
            p = Path(path)
            if p.exists():
                data = yaml.safe_load(p.read_text()) or {}

        cfg = cls(
            sources=data.get("sources", ["sample"]),
            search_terms=data.get("search_terms", list(DEFAULT_SEARCH_TERMS)),
            locations=data.get("locations", ["UK"]),
            max_days_old=int(data.get("max_days_old", 3)),
            ir35_filter=str(data.get("ir35_filter", "inside")).lower(),
            min_match_score=float(data.get("min_match_score", 0.0)),
            max_results=int(data.get("max_results", 50)),
            section_order=data.get("section_order", list(DEFAULT_SECTION_ORDER)),
            output_dir=data.get("output_dir", "output"),
            make_cover_note=bool(data.get("make_cover_note", True)),
            use_llm=bool(data.get("use_llm", False)),
            llm_model=data.get("llm_model", "claude-sonnet-5"),
        )

        # Secrets: env always wins over the (optional) config file.
        cfg.adzuna_app_id = os.getenv("ADZUNA_APP_ID", data.get("adzuna_app_id", ""))
        cfg.adzuna_app_key = os.getenv("ADZUNA_APP_KEY", data.get("adzuna_app_key", ""))
        cfg.reed_api_key = os.getenv("REED_API_KEY", data.get("reed_api_key", ""))
        cfg.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", data.get("anthropic_api_key", ""))
        cfg.anthropic_base_url = os.getenv(
            "ANTHROPIC_BASE_URL", data.get("anthropic_base_url", "https://api.anthropic.com")
        )
        return cfg
