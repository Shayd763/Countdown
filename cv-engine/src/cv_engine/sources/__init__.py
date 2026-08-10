"""Source registry."""

from __future__ import annotations

from ..config import Config
from .adzuna import AdzunaSource
from .base import JobSource
from .reed import ReedSource
from .sample import SampleSource

_REGISTRY: dict[str, type[JobSource]] = {
    SampleSource.name: SampleSource,
    AdzunaSource.name: AdzunaSource,
    ReedSource.name: ReedSource,
}


def build_sources(config: Config) -> list[JobSource]:
    """Instantiate the sources named in the config, skipping unknown names."""
    sources: list[JobSource] = []
    for name in config.sources:
        cls = _REGISTRY.get(name.lower())
        if cls is None:
            continue
        sources.append(cls(config))
    return sources


__all__ = ["JobSource", "build_sources", "SampleSource", "AdzunaSource", "ReedSource"]
