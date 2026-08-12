"""Tailor the master profile into a bespoke CV for a specific job.

The default tailoring is deterministic and offline:

* Re-order skills so the ones the advert asks for come first.
* Re-order and lightly prune experience bullets to surface the most relevant
  achievements for this role.
* Rewrite the professional summary to echo the role title and top skills.

If ``use_llm`` is enabled *and* an Anthropic API key is available, the summary
and bullet selection are upgraded via the Claude API for a genuinely bespoke
result. The LLM path degrades gracefully to the deterministic path on any
error, so a daily run never fails because of the model.
"""

from __future__ import annotations

import copy
import json
import logging

from .config import Config
from .models import ExperienceEntry, Job, Profile

log = logging.getLogger("cv_engine.tailor")


def _reorder_skills(profile: Profile, job: Job) -> list[str]:
    matched = {s.lower() for s in job.matched_skills}
    front = [s for s in profile.skills if s.lower() in matched]
    back = [s for s in profile.skills if s.lower() not in matched]
    return front + back


def _score_bullet(bullet: str, keywords: set[str]) -> int:
    text = bullet.lower()
    return sum(1 for k in keywords if k in text)


def _reorder_experience(profile: Profile, job: Job) -> list[ExperienceEntry]:
    keywords = {s.lower() for s in job.matched_skills}
    keywords |= {w for w in job.title.lower().split() if len(w) > 3}
    reordered: list[ExperienceEntry] = []
    for entry in profile.experience:
        clone = copy.deepcopy(entry)
        # Stable sort: most keyword-relevant bullets first, original order breaks ties.
        clone.bullets = [
            b for _, _, b in sorted(
                (
                    (-_score_bullet(b, keywords), i, b)
                    for i, b in enumerate(clone.bullets)
                ),
                key=lambda t: (t[0], t[1]),
            )
        ]
        reordered.append(clone)
    return reordered


def _deterministic_summary(profile: Profile, job: Job) -> str:
    top = job.matched_skills[:5] or profile.skills[:5]
    skills_phrase = ", ".join(top)
    role = job.title
    base = profile.summary.strip()
    lead = (
        f"{profile.headline or 'Experienced contractor'} targeting the {role} "
        f"engagement at {job.company}."
    )
    strengths = f" Core strengths: {skills_phrase}." if skills_phrase else ""
    return f"{lead}{strengths} {base}".strip()


def _llm_tailor(profile: Profile, job: Job, config: Config) -> dict | None:
    """Optionally call the Claude API for a bespoke summary + bullet picks."""
    try:
        import requests

        prompt = (
            "You are a professional CV writer. Given a candidate profile and a "
            "job advert, write a tailored 2-3 sentence professional summary and "
            "select the 6 most relevant achievement bullets. Respond ONLY with "
            "JSON: {\"summary\": str, \"bullets\": [str, ...]}.\n\n"
            f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n"
            f"JOB DESCRIPTION:\n{job.description[:3000]}\n\n"
            f"CANDIDATE HEADLINE: {profile.headline}\n"
            f"CANDIDATE SKILLS: {', '.join(profile.skills)}\n"
            "CANDIDATE ACHIEVEMENTS:\n"
            + "\n".join(
                f"- {b}" for e in profile.experience for b in e.bullets
            )[:4000]
        )
        resp = requests.post(
            f"{config.anthropic_base_url}/v1/messages",
            headers={
                "x-api-key": config.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.llm_model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        text = "".join(
            block.get("text", "")
            for block in resp.json().get("content", [])
            if block.get("type") == "text"
        )
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        return json.loads(text[start : end + 1])
    except Exception as exc:  # pragma: no cover - network/best effort
        log.warning("LLM tailoring failed, using deterministic path: %s", exc)
        return None


def tailor(profile: Profile, job: Job, config: Config) -> Profile:
    """Return a new Profile customised for ``job`` (the original is untouched)."""
    tailored = copy.deepcopy(profile)
    tailored.skills = _reorder_skills(profile, job)
    tailored.experience = _reorder_experience(profile, job)
    tailored.summary = _deterministic_summary(profile, job)

    if config.use_llm and config.anthropic_api_key:
        result = _llm_tailor(profile, job, config)
        if result:
            if result.get("summary"):
                tailored.summary = result["summary"].strip()
            picked = [b.strip() for b in result.get("bullets", []) if b.strip()]
            if picked:
                # Promote the LLM-selected bullets to the top of the first role
                # while keeping every original bullet available elsewhere.
                for entry in tailored.experience:
                    entry.bullets = sorted(
                        entry.bullets,
                        key=lambda b: 0 if b in picked else 1,
                    )
    return tailored
