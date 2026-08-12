"""Generate a short, editable cover note per application (Markdown)."""

from __future__ import annotations

from .models import Job, Profile


def cover_note(profile: Profile, job: Job) -> str:
    top_skills = job.matched_skills[:4] or profile.skills[:4]
    skills_line = ", ".join(top_skills)
    name = profile.contact.name
    return f"""# Cover note — {job.title} @ {job.company}

Dear Hiring Manager,

I am applying for the **{job.title}** contract ({job.ir35_status} IR35). As a
{profile.headline or 'seasoned contractor'}, I bring directly relevant
experience in {skills_line}.

{profile.summary}

I would welcome the opportunity to discuss how I can deliver for {job.company}.

Kind regards,
{name}
{profile.contact.email}{('  |  ' + profile.contact.phone) if profile.contact.phone else ''}
"""
