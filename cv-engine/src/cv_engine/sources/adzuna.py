"""Adzuna job search adapter.

Adzuna offers a free JSON search API (https://developer.adzuna.com/). Register
for an ``app_id`` and ``app_key`` and set ``ADZUNA_APP_ID`` / ``ADZUNA_APP_KEY``
(or put them in config.yaml). The adapter searches the UK index, restricts to
contract adverts, and returns the full advert text for IR35 classification.
"""

from __future__ import annotations

import logging

import requests

from ..models import Job
from .base import JobSource

log = logging.getLogger("cv_engine.sources.adzuna")

_BASE = "https://api.adzuna.com/v1/api/jobs/gb/search/{page}"


class AdzunaSource(JobSource):
    name = "adzuna"

    def available(self) -> bool:
        return bool(self.config.adzuna_app_id and self.config.adzuna_app_key)

    def fetch(self, terms: list[str], locations: list[str]) -> list[Job]:
        jobs: dict[str, Job] = {}
        query = " ".join(terms) if terms else "contract"
        wheres = locations or [""]
        for where in wheres:
            for page in range(1, 3):  # up to 2 pages per location
                params = {
                    "app_id": self.config.adzuna_app_id,
                    "app_key": self.config.adzuna_app_key,
                    "results_per_page": 25,
                    "what": query,
                    "content-type": "application/json",
                    "max_days_old": self.config.max_days_old,
                    "full_time": 0,
                }
                if where and where.upper() != "UK":
                    params["where"] = where
                resp = requests.get(_BASE.format(page=page), params=params, timeout=30)
                if resp.status_code != 200:
                    log.warning("adzuna page %s status %s", page, resp.status_code)
                    break
                results = resp.json().get("results", [])
                if not results:
                    break
                for r in results:
                    job = self._to_job(r)
                    jobs[job.id] = job
        return list(jobs.values())

    def _to_job(self, r: dict) -> Job:
        return Job.from_dict(
            {
                "title": r.get("title", ""),
                "company": (r.get("company") or {}).get("display_name", ""),
                "location": (r.get("location") or {}).get("display_name", ""),
                "description": r.get("description", ""),
                "url": r.get("redirect_url", ""),
                "source": self.name,
                "contract_type": r.get("contract_time", "") or r.get("contract_type", ""),
                "salary_min": r.get("salary_min"),
                "salary_max": r.get("salary_max"),
                "posted": (r.get("created", "") or "")[:10],
                "external_id": str(r.get("id", "")),
            }
        )
