"""Core data models for the CV application engine.

These lightweight dataclasses describe the two halves of the system:

* The *profile* — the master database of the user's CV and experience.
* The *job* — a discovered contract role, plus derived analysis such as the
  IR35 status and how well it matches the profile.

Everything is plain Python with no third-party dependencies so the models can
be imported anywhere (tests, renderers, sources) without side effects.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any


# --------------------------------------------------------------------------- #
# Profile (the CV database)
# --------------------------------------------------------------------------- #
@dataclass
class Contact:
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    website: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Contact":
        return cls(**{k: data.get(k, "") for k in cls.__dataclass_fields__})


@dataclass
class ExperienceEntry:
    organisation: str = ""
    title: str = ""
    location: str = ""
    start: str = ""            # free-form, e.g. "Jan 2022"
    end: str = ""              # free-form, e.g. "Present"
    bullets: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)   # skills/keywords this role demonstrates

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperienceEntry":
        return cls(
            organisation=data.get("organisation", data.get("company", "")),
            title=data.get("title", data.get("role", "")),
            location=data.get("location", ""),
            start=str(data.get("start", "")),
            end=str(data.get("end", "")),
            bullets=list(data.get("bullets", [])),
            tags=[t.lower() for t in data.get("tags", [])],
        )

    @property
    def date_range(self) -> str:
        if self.start and self.end:
            return f"{self.start} – {self.end}"
        return self.start or self.end


@dataclass
class EducationEntry:
    institution: str = ""
    qualification: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    details: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EducationEntry":
        return cls(
            institution=data.get("institution", data.get("school", "")),
            qualification=data.get("qualification", data.get("degree", "")),
            location=data.get("location", ""),
            start=str(data.get("start", "")),
            end=str(data.get("end", "")),
            details=list(data.get("details", [])),
        )

    @property
    def date_range(self) -> str:
        if self.start and self.end:
            return f"{self.start} – {self.end}"
        return self.end or self.start


@dataclass
class Certification:
    name: str = ""
    issuer: str = ""
    year: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Certification":
        return cls(
            name=data.get("name", ""),
            issuer=data.get("issuer", ""),
            year=str(data.get("year", "")),
        )


@dataclass
class Profile:
    """The master CV database. One profile per person."""

    contact: Contact = field(default_factory=Contact)
    headline: str = ""                       # e.g. "Interim Delivery Manager"
    summary: str = ""                         # base professional summary
    skills: list[str] = field(default_factory=list)
    experience: list[ExperienceEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    certifications: list[Certification] = field(default_factory=list)
    # Free-form preferences that steer discovery.
    target_titles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    min_day_rate: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        return cls(
            contact=Contact.from_dict(data.get("contact", {})),
            headline=data.get("headline", ""),
            summary=data.get("summary", ""),
            skills=[str(s) for s in data.get("skills", [])],
            experience=[ExperienceEntry.from_dict(e) for e in data.get("experience", [])],
            education=[EducationEntry.from_dict(e) for e in data.get("education", [])],
            certifications=[Certification.from_dict(c) for c in data.get("certifications", [])],
            target_titles=[str(t) for t in data.get("target_titles", [])],
            locations=[str(l) for l in data.get("locations", [])],
            min_day_rate=data.get("min_day_rate"),
        )

    @property
    def skill_set(self) -> set[str]:
        return {s.lower().strip() for s in self.skills if s.strip()}


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
IR35_INSIDE = "inside"
IR35_OUTSIDE = "outside"
IR35_UNKNOWN = "unknown"


@dataclass
class Job:
    """A discovered contract role plus derived analysis."""

    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    url: str = ""                      # apply / redirect link
    source: str = ""                   # which adapter found it
    contract_type: str = ""            # "contract", "permanent", ...
    day_rate: str = ""                 # free-form as advertised
    salary_min: float | None = None
    salary_max: float | None = None
    posted: str = ""                   # ISO date string if known
    external_id: str = ""              # source-provided id, if any

    # Derived (filled in by the pipeline)
    ir35_status: str = IR35_UNKNOWN
    match_score: float = 0.0
    matched_skills: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def id(self) -> str:
        """Stable identifier used for dedupe and output folder names."""
        if self.external_id:
            basis = f"{self.source}:{self.external_id}"
        else:
            basis = f"{self.source}:{self.company}:{self.title}:{self.url}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]

    @property
    def slug(self) -> str:
        raw = f"{self.company}-{self.title}".lower()
        raw = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
        return raw[:60] or "role"

    @property
    def haystack(self) -> str:
        """Lower-cased searchable text for classification/matching."""
        return " ".join([self.title, self.company, self.description]).lower()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self.__dataclass_fields__:
            out[name] = getattr(self, name)
        out["id"] = self.id
        return out
