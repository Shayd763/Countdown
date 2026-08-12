"""Load and validate the CV database (the master profile YAML)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import Profile

# Contact fields can be supplied via environment variables so that sensitive
# personal details (phone, street address) need never be committed to a public
# repository. A non-empty env value overrides whatever is in the YAML.
_CONTACT_ENV = {
    "name": "CV_CONTACT_NAME",
    "email": "CV_CONTACT_EMAIL",
    "phone": "CV_CONTACT_PHONE",
    "location": "CV_CONTACT_LOCATION",
    "linkedin": "CV_CONTACT_LINKEDIN",
    "website": "CV_CONTACT_WEBSITE",
}


class ProfileError(ValueError):
    pass


def _apply_contact_env(profile: Profile) -> None:
    for field_name, env_name in _CONTACT_ENV.items():
        value = os.getenv(env_name, "").strip()
        if value:
            setattr(profile.contact, field_name, value)


def load_profile(path: str | Path) -> Profile:
    p = Path(path)
    if not p.exists():
        raise ProfileError(
            f"Profile not found at {p}. Run `cv-engine init` to scaffold one, "
            f"then fill in your CV details."
        )
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise ProfileError(f"Profile at {p} must be a YAML mapping.")
    profile = Profile.from_dict(data)
    _apply_contact_env(profile)
    validate_profile(profile)
    return profile


def validate_profile(profile: Profile) -> list[str]:
    """Return a list of human-readable warnings (empty means all good)."""
    warnings: list[str] = []
    if not profile.contact.name:
        raise ProfileError("Profile is missing contact.name — this is required.")
    if not profile.contact.email:
        warnings.append("No contact.email set — recruiters won't be able to reach you.")
    if not profile.experience:
        warnings.append("No experience entries — the CV will be very thin.")
    if not profile.skills:
        warnings.append("No skills listed — job matching will score everything 0.")
    return warnings
