"""Render a tailored profile as a Harvard-format CV.

Two outputs are produced per job:

* ``cv.pdf`` — a print-ready PDF laid out in the classic Harvard resume style
  (centered name + contact header, small-caps section headings under full-width
  rules, right-aligned dates, action-verb bullets). Built with fpdf2, which is
  pure-Python — no system libraries, no headless browser.
* ``cv.html`` — the same content as a self-contained HTML file for quick
  preview in the dashboard.

The Harvard template conventions implemented here follow the Harvard FAS/OCS
resume guide: single column, reverse-chronological, consistent typography.
"""

from __future__ import annotations

import html
from pathlib import Path

from fpdf import FPDF

from .config import Config
from .models import Profile

# Harvard-style geometry (millimetres / points).
MARGIN = 15.0
NAME_SIZE = 20
CONTACT_SIZE = 9.5
SECTION_SIZE = 11
BODY_SIZE = 10
LINE = 4.6


class _HarvardPDF(FPDF):
    def header(self) -> None:  # no running header
        pass

    def footer(self) -> None:  # no running footer
        pass


def _txt(pdf: FPDF, text: str) -> str:
    """fpdf2 core fonts are latin-1; replace common unicode punctuation."""
    replacements = {
        "–": "-", "—": "-", "’": "'", "‘": "'",
        "“": '"', "”": '"', "•": "-", "…": "...",
        " ": " ", "é": "e", "−": "-",
    }
    for a, b in replacements.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


def _section_header(pdf: _HarvardPDF, title: str) -> None:
    pdf.ln(1.5)
    pdf.set_font("Helvetica", "B", SECTION_SIZE)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 5.5, _txt(pdf, title.upper()), new_x="LMARGIN", new_y="NEXT")
    y = pdf.get_y()
    pdf.set_draw_color(20, 20, 20)
    pdf.set_line_width(0.4)
    pdf.line(MARGIN, y, pdf.w - MARGIN, y)
    pdf.ln(1.5)


def _bullets(pdf: _HarvardPDF, bullets: list[str]) -> None:
    pdf.set_font("Helvetica", "", BODY_SIZE)
    pdf.set_text_color(0, 0, 0)
    usable = pdf.w - 2 * MARGIN
    for bullet in bullets:
        x_start = pdf.get_x()
        pdf.cell(4, LINE, "-", new_x="RIGHT", new_y="TOP")
        pdf.multi_cell(usable - 4, LINE, _txt(pdf, bullet), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(x_start)


def _render_summary(pdf: _HarvardPDF, profile: Profile) -> None:
    if not profile.summary:
        return
    _section_header(pdf, "Professional Summary")
    pdf.set_font("Helvetica", "", BODY_SIZE)
    pdf.multi_cell(0, LINE, _txt(pdf, profile.summary), new_x="LMARGIN", new_y="NEXT")


def _render_skills(pdf: _HarvardPDF, profile: Profile) -> None:
    if not profile.skills:
        return
    _section_header(pdf, "Core Skills")
    pdf.set_font("Helvetica", "", BODY_SIZE)
    pdf.multi_cell(0, LINE, _txt(pdf, "  |  ".join(profile.skills)), new_x="LMARGIN", new_y="NEXT")


def _entry_heading(pdf: _HarvardPDF, left_bold: str, right: str) -> None:
    """Bold left title with a right-aligned date on the same baseline."""
    pdf.set_font("Helvetica", "B", BODY_SIZE)
    right_w = pdf.get_string_width(_txt(pdf, right)) + 1
    pdf.cell(pdf.w - 2 * MARGIN - right_w, LINE, _txt(pdf, left_bold), new_x="RIGHT", new_y="TOP")
    pdf.set_font("Helvetica", "", BODY_SIZE)
    pdf.cell(right_w, LINE, _txt(pdf, right), align="R", new_x="LMARGIN", new_y="NEXT")


def _render_experience(pdf: _HarvardPDF, profile: Profile) -> None:
    if not profile.experience:
        return
    _section_header(pdf, "Experience")
    for e in profile.experience:
        _entry_heading(pdf, e.organisation, e.date_range)
        subtitle = " | ".join(x for x in [e.title, e.location] if x)
        if subtitle:
            pdf.set_font("Helvetica", "I", BODY_SIZE)
            pdf.cell(0, LINE, _txt(pdf, subtitle), new_x="LMARGIN", new_y="NEXT")
        _bullets(pdf, e.bullets)
        pdf.ln(1)


def _render_education(pdf: _HarvardPDF, profile: Profile) -> None:
    if not profile.education:
        return
    _section_header(pdf, "Education")
    for ed in profile.education:
        _entry_heading(pdf, ed.institution, ed.date_range)
        line = " | ".join(x for x in [ed.qualification, ed.location] if x)
        if line:
            pdf.set_font("Helvetica", "I", BODY_SIZE)
            pdf.cell(0, LINE, _txt(pdf, line), new_x="LMARGIN", new_y="NEXT")
        _bullets(pdf, ed.details)
        pdf.ln(1)


def _render_certifications(pdf: _HarvardPDF, profile: Profile) -> None:
    if not profile.certifications:
        return
    _section_header(pdf, "Certifications")
    pdf.set_font("Helvetica", "", BODY_SIZE)
    for c in profile.certifications:
        parts = [c.name]
        if c.issuer:
            parts.append(c.issuer)
        if c.year:
            parts.append(str(c.year))
        pdf.multi_cell(0, LINE, _txt(pdf, "  -  ".join(parts)), new_x="LMARGIN", new_y="NEXT")


_RENDERERS = {
    "summary": _render_summary,
    "skills": _render_skills,
    "experience": _render_experience,
    "education": _render_education,
    "certifications": _render_certifications,
}


def render_pdf(profile: Profile, config: Config, out_path: str | Path) -> Path:
    pdf = _HarvardPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=MARGIN)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.add_page()

    # Header: centered name + contact line (classic Harvard).
    c = profile.contact
    pdf.set_font("Helvetica", "B", NAME_SIZE)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 9, _txt(pdf, c.name), align="C", new_x="LMARGIN", new_y="NEXT")

    contact_bits = [b for b in [c.location, c.phone, c.email, c.linkedin, c.website] if b]
    if contact_bits:
        pdf.set_font("Helvetica", "", CONTACT_SIZE)
        pdf.set_text_color(60, 60, 60)
        pdf.cell(0, 5, _txt(pdf, "  |  ".join(contact_bits)), align="C",
                 new_x="LMARGIN", new_y="NEXT")
    if profile.headline:
        pdf.set_font("Helvetica", "I", CONTACT_SIZE + 0.5)
        pdf.cell(0, 5, _txt(pdf, profile.headline), align="C",
                 new_x="LMARGIN", new_y="NEXT")

    for section in config.section_order:
        renderer = _RENDERERS.get(section)
        if renderer:
            renderer(pdf, profile)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))
    return out


# --------------------------------------------------------------------------- #
# HTML (for the dashboard preview)
# --------------------------------------------------------------------------- #
def _esc(text: str) -> str:
    return html.escape(text or "")


def render_html(profile: Profile, config: Config, out_path: str | Path) -> Path:
    c = profile.contact
    contact_bits = [b for b in [c.location, c.phone, c.email, c.linkedin, c.website] if b]
    parts: list[str] = []

    def section_html(name: str) -> str:
        if name == "summary" and profile.summary:
            return f"<h2>Professional Summary</h2><p>{_esc(profile.summary)}</p>"
        if name == "skills" and profile.skills:
            return "<h2>Core Skills</h2><p class='skills'>" + \
                "  |  ".join(_esc(s) for s in profile.skills) + "</p>"
        if name == "experience" and profile.experience:
            rows = []
            for e in profile.experience:
                bl = "".join(f"<li>{_esc(b)}</li>" for b in e.bullets)
                sub = _esc(" | ".join(x for x in [e.title, e.location] if x))
                rows.append(
                    f"<div class='entry'><div class='row'><span class='org'>"
                    f"{_esc(e.organisation)}</span><span class='date'>"
                    f"{_esc(e.date_range)}</span></div><div class='sub'>{sub}</div>"
                    f"<ul>{bl}</ul></div>"
                )
            return "<h2>Experience</h2>" + "".join(rows)
        if name == "education" and profile.education:
            rows = []
            for ed in profile.education:
                dl = "".join(f"<li>{_esc(d)}</li>" for d in ed.details)
                sub = _esc(" | ".join(x for x in [ed.qualification, ed.location] if x))
                rows.append(
                    f"<div class='entry'><div class='row'><span class='org'>"
                    f"{_esc(ed.institution)}</span><span class='date'>"
                    f"{_esc(ed.date_range)}</span></div><div class='sub'>{sub}</div>"
                    f"<ul>{dl}</ul></div>"
                )
            return "<h2>Education</h2>" + "".join(rows)
        if name == "certifications" and profile.certifications:
            items = "".join(
                f"<li>{_esc('  -  '.join(x for x in [cc.name, cc.issuer, str(cc.year)] if x))}</li>"
                for cc in profile.certifications
            )
            return f"<h2>Certifications</h2><ul>{items}</ul>"
        return ""

    for section in config.section_order:
        parts.append(section_html(section))

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{_esc(c.name)} - CV</title>
<style>
  body {{ font-family: Georgia, 'Times New Roman', serif; max-width: 800px;
          margin: 32px auto; color: #111; line-height: 1.45; padding: 0 24px; }}
  h1 {{ text-align: center; margin: 0; font-size: 28px; letter-spacing: .5px; }}
  .contact {{ text-align: center; color: #555; font-size: 13px; margin: 6px 0; }}
  .headline {{ text-align: center; font-style: italic; color: #444; margin-bottom: 8px; }}
  h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: 1px;
        border-bottom: 1.5px solid #111; padding-bottom: 3px; margin: 20px 0 8px; }}
  .row {{ display: flex; justify-content: space-between; font-weight: bold; }}
  .sub {{ font-style: italic; color: #333; margin-bottom: 2px; }}
  .date {{ font-weight: normal; color: #333; }}
  ul {{ margin: 4px 0 8px 18px; padding: 0; }}
  li {{ margin-bottom: 2px; }}
  .skills {{ margin: 4px 0; }}
  .entry {{ margin-bottom: 10px; }}
</style></head><body>
<h1>{_esc(c.name)}</h1>
<div class="contact">{_esc('  |  '.join(contact_bits))}</div>
{f'<div class="headline">{_esc(profile.headline)}</div>' if profile.headline else ''}
{''.join(parts)}
</body></html>"""

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return out
