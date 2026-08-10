"""Job discovery: fetch from all sources, classify IR35, filter, dedupe, rank."""

from __future__ import annotations

import logging

from .config import Config
from .ir35 import classify_job, matches_filter
from .matching import rank
from .models import Job, Profile
from .sources import build_sources

log = logging.getLogger("cv_engine.discovery")


def _dedupe(jobs: list[Job]) -> list[Job]:
    seen: dict[str, Job] = {}
    for job in jobs:
        # Prefer the first occurrence but keep the longest description.
        existing = seen.get(job.id)
        if existing is None or len(job.description) > len(existing.description):
            seen[job.id] = job
    return list(seen.values())


def discover(config: Config, profile: Profile) -> list[Job]:
    """Run the full discovery pipeline and return ranked, filtered jobs."""
    terms = config.search_terms or profile.target_titles or ["contract"]
    locations = config.locations or profile.locations or ["UK"]

    raw: list[Job] = []
    for source in build_sources(config):
        raw.extend(source.safe_fetch(terms, locations))

    jobs = _dedupe(raw)
    log.info("discovered %d unique jobs before filtering", len(jobs))

    # Classify IR35 and apply the configured filter.
    filtered: list[Job] = []
    for job in jobs:
        classify_job(job)
        if not matches_filter(job.ir35_status, config.ir35_filter):
            continue
        filtered.append(job)

    ranked = rank(filtered, profile)
    ranked = [j for j in ranked if j.match_score >= config.min_match_score]
    ranked = ranked[: config.max_results]
    log.info("%d jobs pass IR35=%s and score>=%s", len(ranked), config.ir35_filter, config.min_match_score)
    return ranked
