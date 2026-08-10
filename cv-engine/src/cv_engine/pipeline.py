"""End-to-end daily pipeline.

1. Load profile + config.
2. Discover inside-IR35 contract roles.
3. For each role, tailor the CV and render a Harvard-format PDF + HTML.
4. Emit a dashboard (dashboard.html), a machine-readable applications.csv and a
   jobs.json manifest into a dated output folder — everything the user needs to
   open each advert, upload the matching CV, and click apply.
"""

from __future__ import annotations

import csv
import datetime as _dt
import html
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .cover import cover_note
from .discovery import discover
from .models import Job, Profile
from .render import render_html, render_pdf
from .tailor import tailor

log = logging.getLogger("cv_engine.pipeline")


@dataclass
class Application:
    job: Job
    folder: Path
    pdf: Path
    html: Path
    cover: Path | None


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def run_pipeline(profile: Profile, config: Config, run_date: str | None = None) -> Path:
    date_str = run_date or _dt.date.today().isoformat()
    root = Path(config.output_dir) / date_str
    jobs_root = root / "jobs"
    jobs_root.mkdir(parents=True, exist_ok=True)

    jobs = discover(config, profile)
    log.info("building %d applications", len(jobs))

    applications: list[Application] = []
    for job in jobs:
        folder = jobs_root / f"{job.id}-{job.slug}"
        folder.mkdir(parents=True, exist_ok=True)

        tailored = tailor(profile, job, config)
        pdf = render_pdf(tailored, config, folder / "cv.pdf")
        html_cv = render_html(tailored, config, folder / "cv.html")

        (folder / "job.json").write_text(
            json.dumps(job.to_dict(), indent=2, default=str), encoding="utf-8"
        )

        cover_path: Path | None = None
        if config.make_cover_note:
            cover_path = folder / "cover_note.md"
            cover_path.write_text(cover_note(tailored, job), encoding="utf-8")

        applications.append(Application(job, folder, pdf, html_cv, cover_path))

    _write_csv(root / "applications.csv", applications)
    _write_manifest(root / "jobs.json", applications, date_str)
    dashboard = _write_dashboard(root / "dashboard.html", applications, date_str, config)
    _update_index(Path(config.output_dir))
    log.info("done -> %s", dashboard)
    return dashboard


def _rel(base: Path, target: Path) -> str:
    try:
        return str(target.relative_to(base))
    except ValueError:
        return str(target)


def _write_csv(path: Path, apps: list[Application]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["match_score", "title", "company", "location", "ir35",
             "day_rate", "apply_url", "cv_pdf", "cover_note", "source"]
        )
        for app in apps:
            j = app.job
            writer.writerow([
                j.match_score, j.title, j.company, j.location, j.ir35_status,
                j.day_rate, j.url, _rel(path.parent, app.pdf),
                _rel(path.parent, app.cover) if app.cover else "", j.source,
            ])


def _write_manifest(path: Path, apps: list[Application], date_str: str) -> None:
    manifest = {
        "generated": date_str,
        "count": len(apps),
        "applications": [
            {
                **app.job.to_dict(),
                "cv_pdf": _rel(path.parent, app.pdf),
                "cv_html": _rel(path.parent, app.html),
                "cover_note": _rel(path.parent, app.cover) if app.cover else None,
            }
            for app in apps
        ],
    }
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


def _write_dashboard(path: Path, apps: list[Application], date_str: str, config: Config) -> Path:
    rows = []
    for app in apps:
        j = app.job
        matched = ", ".join(j.matched_skills[:6])
        rows.append(f"""
      <tr>
        <td class="score">{j.match_score:.0f}</td>
        <td><strong>{_esc(j.title)}</strong><div class="muted">{_esc(matched)}</div></td>
        <td>{_esc(j.company)}</td>
        <td>{_esc(j.location)}</td>
        <td><span class="ir35 {(_esc(j.ir35_status))}">{_esc(j.ir35_status)}</span></td>
        <td>{_esc(j.day_rate)}</td>
        <td class="actions">
          <a class="btn preview" href="{_esc(_rel(path.parent, app.html))}" target="_blank">Preview CV</a>
          <a class="btn pdf" href="{_esc(_rel(path.parent, app.pdf))}" target="_blank" download>Download PDF</a>
          <a class="btn apply" href="{_esc(j.url)}" target="_blank" rel="noopener">Apply &rarr;</a>
        </td>
      </tr>""")

    table = "".join(rows) or (
        '<tr><td colspan="7" class="muted" style="text-align:center;padding:24px">'
        'No matching roles today. Try widening search_terms or lowering '
        'min_match_score in config.yaml.</td></tr>'
    )

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CV Application Dashboard — {date_str}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0;
          background: #0f172a; color: #e2e8f0; }}
  header {{ padding: 24px 32px; background: #111c33; border-bottom: 1px solid #223; }}
  h1 {{ margin: 0; font-size: 22px; }}
  .sub {{ color: #94a3b8; margin-top: 4px; font-size: 14px; }}
  main {{ padding: 24px 32px; overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; min-width: 900px; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #223; vertical-align: top; }}
  th {{ color: #94a3b8; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
  .score {{ font-weight: 700; font-size: 18px; color: #38bdf8; }}
  .muted {{ color: #64748b; font-size: 12px; margin-top: 3px; }}
  .ir35 {{ padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
  .ir35.inside {{ background: #7f1d1d; color: #fecaca; }}
  .ir35.outside {{ background: #14532d; color: #bbf7d0; }}
  .ir35.unknown {{ background: #334155; color: #cbd5e1; }}
  .actions {{ white-space: nowrap; }}
  .btn {{ display: inline-block; padding: 6px 12px; border-radius: 6px; text-decoration: none;
          font-size: 13px; margin: 0 2px; font-weight: 600; }}
  .btn.preview {{ background: #1e293b; color: #e2e8f0; }}
  .btn.pdf {{ background: #334155; color: #e2e8f0; }}
  .btn.apply {{ background: #38bdf8; color: #0f172a; }}
  a.btn:hover {{ opacity: .85; }}
</style></head><body>
<header>
  <h1>CV Application Dashboard</h1>
  <div class="sub">{date_str} &middot; {len(apps)} tailored application(s) &middot;
    IR35 filter: <strong>{_esc(config.ir35_filter)}</strong> &middot;
    Sorted by match score</div>
</header>
<main>
  <table>
    <thead><tr>
      <th>Score</th><th>Role</th><th>Company</th><th>Location</th>
      <th>IR35</th><th>Rate</th><th>Actions</th>
    </tr></thead>
    <tbody>{table}</tbody>
  </table>
</main>
</body></html>"""
    path.write_text(doc, encoding="utf-8")
    return path


def _update_index(output_dir: Path) -> None:
    """Write a top-level index.html linking to each dated run (newest first)."""
    if not output_dir.exists():
        return
    runs = sorted(
        (p.name for p in output_dir.iterdir() if p.is_dir() and (p / "dashboard.html").exists()),
        reverse=True,
    )
    links = "".join(
        f'<li><a href="{r}/dashboard.html">{r}</a></li>' for r in runs
    ) or "<li>No runs yet.</li>"
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CV Application Engine — Runs</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 48px auto;
          padding: 0 24px; background: #0f172a; color: #e2e8f0; }}
  a {{ color: #38bdf8; }} h1 {{ font-size: 22px; }}
  li {{ margin: 6px 0; font-size: 16px; }}
</style></head><body>
<h1>CV Application Engine</h1>
<p>Daily runs (newest first):</p>
<ul>{links}</ul>
</body></html>"""
    (output_dir / "index.html").write_text(doc, encoding="utf-8")
