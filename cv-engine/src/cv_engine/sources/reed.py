"""Reed.co.uk job search adapter.

Reed offers a free JSON API (https://www.reed.co.uk/developers). Register for
an API key and set ``REED_API_KEY`` (or put it in config.yaml). The key is used
as the HTTP Basic Auth username with an empty password. The search endpoint
returns short descriptions; the adapter optionally fetches the full advert per
job so IR35 phrases in the body are visible to the classifier.
"""

from __future__ import annotations

import logging

import requests

from ..models import Job
from .base import JobSource

log = logging.getLogger("cv_engine.sources.reed")

_SEARCH = "https://www.reed.co.uk/api/1.0/search"
_DETAIL = "https://www.reed.co.uk/api/1.0/jobs/{id}"


class ReedSource(JobSource):
    name = "reed"

    def available(self) -> bool:
        return bool(self.config.reed_api_key)

    def fetch(self, terms: list[str], locations: list[str]) -> list[Job]:
        auth = (self.config.reed_api_key, "")
        jobs: dict[str, Job] = {}
        keywords = " ".join(terms) if terms else "contract"
        wheres = locations or [""]
        for where in wheres:
            params = {
                "keywords": keywords,
                "contract": "true",
                "resultsToTake": 50,
            }
            if where and where.upper() != "UK":
                params["locationName"] = where
                params["distanceFromLocation"] = 15
            resp = requests.get(_SEARCH, params=params, auth=auth, timeout=30)
            if resp.status_code != 200:
                log.warning("reed search status %s", resp.status_code)
                continue
            for r in resp.json().get("results", []):
                job = self._to_job(r, auth)
                jobs[job.id] = job
        return list(jobs.values())

    def _to_job(self, r: dict, auth: tuple[str, str]) -> Job:
        description = r.get("jobDescription", "")
        job_id = r.get("jobId")
        # Fetch full advert body so IR35 phrasing is available.
        try:
            detail = requests.get(_DETAIL.format(id=job_id), auth=auth, timeout=30)
            if detail.status_code == 200:
                description = detail.json().get("jobDescription", description)
        except requests.RequestException:  # pragma: no cover - network best effort
            pass
        return Job.from_dict(
            {
                "title": r.get("jobTitle", ""),
                "company": r.get("employerName", ""),
                "location": r.get("locationName", ""),
                "description": description,
                "url": r.get("jobUrl", f"https://www.reed.co.uk/jobs/{job_id}"),
                "source": self.name,
                "contract_type": "contract",
                "salary_min": r.get("minimumSalary"),
                "salary_max": r.get("maximumSalary"),
                "posted": (r.get("date", "") or ""),
                "external_id": str(job_id or ""),
            }
        )
