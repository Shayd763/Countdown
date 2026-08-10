"""Score how well a discovered job matches the user's profile.

The score is intentionally transparent (no ML black box): it rewards profile
skills that appear in the advert, gives a bonus for target-title overlap, and
normalises to a 0–100 range. ``matched_skills`` is also captured so the
tailoring step knows which skills to surface on the bespoke CV.
"""

from __future__ import annotations

import re

from .models import Job, Profile

_WORD = re.compile(r"[a-z0-9\+\#\.]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _skill_in_text(skill: str, text: str) -> bool:
    """Whole-token / phrase membership test that tolerates multi-word skills."""
    skill = skill.lower().strip()
    if not skill:
        return False
    # Multi-word skills: require the phrase to appear.
    if " " in skill:
        return skill in text
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", text))


def score_job(job: Job, profile: Profile) -> Job:
    """Populate ``job.match_score`` (0–100) and ``job.matched_skills``."""
    text = job.haystack
    matched = [s for s in profile.skills if _skill_in_text(s, text)]
    job.matched_skills = matched

    total_skills = max(len(profile.skill_set), 1)
    skill_coverage = len(matched) / total_skills            # 0..1

    # Title relevance: overlap between target titles and the advert title.
    title_tokens = _tokens(job.title)
    title_hit = 0.0
    for target in profile.target_titles:
        tt = _tokens(target)
        if tt and tt & title_tokens:
            title_hit = max(title_hit, len(tt & title_tokens) / len(tt))

    # Weighted blend, then scale to 0–100. Skill coverage tends to be small
    # even for great matches, so it is amplified before blending.
    raw = 0.7 * min(skill_coverage * 3.0, 1.0) + 0.3 * title_hit
    job.match_score = round(raw * 100, 1)
    return job


def rank(jobs: list[Job], profile: Profile) -> list[Job]:
    for job in jobs:
        score_job(job, profile)
    return sorted(jobs, key=lambda j: j.match_score, reverse=True)
