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
        "title": "Interim Delivery Manager (Inside IR35)",
        "company": "Whitehall Digital Agency",
        "location": "London / Hybrid",
        "contract_type": "contract",
        "day_rate": "£550/day",
        "posted": "2026-08-09",
        "external_id": "sample-001",
        "url": "https://example.com/jobs/sample-001",
        "description": (
            "6-month contract, INSIDE IR35, for an experienced Delivery Manager "
            "to lead a multi-disciplinary agile team delivering GDS-aligned public "
            "services. You will run agile ceremonies, manage stakeholders, own the "
            "delivery roadmap and report on RAID. Experience with Scrum, Kanban, "
            "Jira, roadmapping and stakeholder management essential. SC clearance "
            "desirable. Umbrella company engagement."
        ),
    },
    {
        "title": "Programme Manager - Digital Transformation",
        "company": "NHS Shared Services",
        "location": "Leeds / Remote",
        "contract_type": "contract",
        "day_rate": "£600/day",
        "posted": "2026-08-08",
        "external_id": "sample-002",
        "url": "https://example.com/jobs/sample-002",
        "description": (
            "Inside IR35 programme manager contract to lead a large digital "
            "transformation programme. Governance, benefits realisation, budget "
            "management, stakeholder management and supplier management. Prince2 or "
            "MSP certification preferred. Strong track record delivering complex "
            "change in the public sector."
        ),
    },
    {
        "title": "Senior Product Manager (Outside IR35)",
        "company": "FinTech Scaleup Ltd",
        "location": "Remote (UK)",
        "contract_type": "contract",
        "day_rate": "£650/day",
        "posted": "2026-08-07",
        "external_id": "sample-003",
        "url": "https://example.com/jobs/sample-003",
        "description": (
            "Outside IR35 contract for a Senior Product Manager to own discovery "
            "and delivery of a payments product. Roadmapping, user research, agile "
            "delivery, data-informed prioritisation. This is a genuine B2B "
            "engagement outside IR35."
        ),
    },
    {
        "title": "Scrum Master / Agile Delivery Lead",
        "company": "Central Government Department",
        "location": "Bristol / Hybrid",
        "contract_type": "contract",
        "day_rate": "£475/day",
        "posted": "2026-08-09",
        "external_id": "sample-004",
        "url": "https://example.com/jobs/sample-004",
        "description": (
            "Contract Scrum Master, determined INSIDE IR35 via PAYE only. Coach "
            "agile teams, remove blockers, facilitate Scrum ceremonies, and drive "
            "continuous improvement. Jira, Confluence, servant leadership. Active "
            "SC clearance required."
        ),
    },
    {
        "title": "Permanent Head of Delivery",
        "company": "Retail Software Co",
        "location": "Manchester",
        "contract_type": "permanent",
        "salary_min": 90000,
        "salary_max": 110000,
        "posted": "2026-08-05",
        "external_id": "sample-005",
        "url": "https://example.com/jobs/sample-005",
        "description": (
            "Permanent leadership role owning a delivery function of 40 people. "
            "Not a contract. Line management, hiring, delivery strategy."
        ),
    },
]


class SampleSource(JobSource):
    name = "sample"

    def available(self) -> bool:
        return True

    def fetch(self, terms: list[str], locations: list[str]) -> list[Job]:
        return [Job.from_dict({**d, "source": self.name}) for d in _SAMPLE_JOBS]
