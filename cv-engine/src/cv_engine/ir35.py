"""IR35 status classification.

IR35 is UK tax legislation. A contract advertised as *inside IR35* means the
worker is taxed like an employee; *outside IR35* means they operate as a
genuine business. Job adverts usually state this explicitly, so classification
is primarily phrase-matching with a few normalising rules.
"""

from __future__ import annotations

import re

from .models import IR35_INSIDE, IR35_OUTSIDE, IR35_UNKNOWN, Job

# Ordered most-specific first. Each entry: (compiled regex, status).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\boutside\s*(of\s*)?ir[\s\-]?35\b", re.I), IR35_OUTSIDE),
    (re.compile(r"\bir[\s\-]?35\s*[:\-]?\s*outside\b", re.I), IR35_OUTSIDE),
    (re.compile(r"\binside\s*(of\s*)?ir[\s\-]?35\b", re.I), IR35_INSIDE),
    (re.compile(r"\bir[\s\-]?35\s*[:\-]?\s*inside\b", re.I), IR35_INSIDE),
    (re.compile(r"\bumbrella\s+(company|only|basis)\b", re.I), IR35_INSIDE),
    (re.compile(r"\bpaye\s+(only|contract|basis)\b", re.I), IR35_INSIDE),
    (re.compile(r"\bdetermined\s+inside\b", re.I), IR35_INSIDE),
]


def classify(text: str) -> str:
    """Return inside/outside/unknown for a block of advert text."""
    if not text:
        return IR35_UNKNOWN
    for pattern, status in _PATTERNS:
        if pattern.search(text):
            return status
    return IR35_UNKNOWN


def classify_job(job: Job) -> str:
    status = classify(job.haystack)
    job.ir35_status = status
    return status


def matches_filter(status: str, ir35_filter: str) -> bool:
    """Whether a job's IR35 status passes the configured filter."""
    ir35_filter = (ir35_filter or "any").lower()
    if ir35_filter in ("any", "all", ""):
        return True
    return status == ir35_filter
