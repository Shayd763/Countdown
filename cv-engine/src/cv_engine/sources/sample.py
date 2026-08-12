"""Offline sample source.

Ships a handful of realistic UK contract adverts (a mix of inside/outside IR35
and permanent) so the engine runs end-to-end with no API keys and no network.
Great for demos, tests and first-run onboarding.
"""

from __future__ import annotations

from ..models import Job
from .base import JobSource

_SAMPLE_JOBS = [
    {
        "title": "Infrastructure Project Manager (Inside IR35)",
        "company": "Network Rail",
        "location": "London / Hybrid",
        "contract_type": "contract",
        "day_rate": "£550/day",
        "posted": "2026-08-09",
        "external_id": "sample-001",
        "url": "https://example.com/jobs/sample-001",
        "description": (
            "6-month contract, INSIDE IR35, for an experienced Project Manager to "
            "lead delivery of rail depot infrastructure renovation works. You will "
            "own the NEC3/NEC4 contract, manage compensation events, contractor "
            "KPIs and risk registers, and control project cost baselines. Strong "
            "stakeholder management across a public sector landscape essential. "
            "Umbrella company engagement."
        ),
    },
    {
        "title": "Programme Manager - Digital Transformation",
        "company": "Department for Transport",
        "location": "Leeds / Remote",
        "contract_type": "contract",
        "day_rate": "£600/day",
        "posted": "2026-08-08",
        "external_id": "sample-002",
        "url": "https://example.com/jobs/sample-002",
        "description": (
            "Inside IR35 programme manager contract to lead a public sector "
            "digital transformation programme. Governance, benefits realisation, "
            "cost management, stakeholder management and supplier management. "
            "Experience with PowerBI reporting, data analytics and public fund "
            "assurance highly desirable. Strong track record delivering complex "
            "change in the public sector."
        ),
    },
    {
        "title": "Senior Commercial Manager (Outside IR35)",
        "company": "Tier-1 Construction Ltd",
        "location": "Remote (UK)",
        "contract_type": "contract",
        "day_rate": "£650/day",
        "posted": "2026-08-07",
        "external_id": "sample-003",
        "url": "https://example.com/jobs/sample-003",
        "description": (
            "Outside IR35 contract for a Commercial Manager to administer NEC4 "
            "contracts on a major aviation capital programme. Compensation events, "
            "procurement, cost estimation and contractor KPIs. This is a genuine "
            "B2B engagement outside IR35."
        ),
    },
    {
        "title": "Project Manager - Aviation Capital Works",
        "company": "Major UK Airport Group",
        "location": "Manchester / Hybrid",
        "contract_type": "contract",
        "day_rate": "£525/day",
        "posted": "2026-08-09",
        "external_id": "sample-004",
        "url": "https://example.com/jobs/sample-004",
        "description": (
            "Contract Project Manager, determined INSIDE IR35 via PAYE only. Lead "
            "full-lifecycle delivery (RIBA 1-7) of airport security and capital "
            "expansion works. Manage NEC4 ECC Option A contracts, risk registers, "
            "procurement and utility diversions under live terminal operations. "
            "Aviation or infrastructure background preferred."
        ),
    },
    {
        "title": "Permanent Head of PMO",
        "company": "Retail Software Co",
        "location": "Manchester",
        "contract_type": "permanent",
        "salary_min": 90000,
        "salary_max": 110000,
        "posted": "2026-08-05",
        "external_id": "sample-005",
        "url": "https://example.com/jobs/sample-005",
        "description": (
            "Permanent leadership role owning a PMO function of 40 people. Not a "
            "contract. Line management, hiring, portfolio governance."
        ),
    },
]


class SampleSource(JobSource):
    name = "sample"

    def available(self) -> bool:
        return True

    def fetch(self, terms: list[str], locations: list[str]) -> list[Job]:
        return [Job.from_dict({**d, "source": self.name}) for d in _SAMPLE_JOBS]
