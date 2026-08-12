"""Job source abstraction.

A source knows how to fetch raw job adverts for a set of search terms and
locations, and yield :class:`~cv_engine.models.Job` objects. Sources should be
resilient: on network/credential errors they log and return an empty list
rather than crashing the whole daily run.
"""

from __future__ import annotations

import abc
import logging

from ..config import Config
from ..models import Job

log = logging.getLogger("cv_engine.sources")


class JobSource(abc.ABC):
    name: str = "base"

    def __init__(self, config: Config) -> None:
        self.config = config

    @abc.abstractmethod
    def available(self) -> bool:
        """Whether the source has what it needs (e.g. API keys) to run."""

    @abc.abstractmethod
    def fetch(self, terms: list[str], locations: list[str]) -> list[Job]:
        ...

    def safe_fetch(self, terms: list[str], locations: list[str]) -> list[Job]:
        if not self.available():
            log.info("source %s skipped (not configured)", self.name)
            return []
        try:
            jobs = self.fetch(terms, locations)
            log.info("source %s returned %d jobs", self.name, len(jobs))
            return jobs
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("source %s failed: %s", self.name, exc)
            return []
