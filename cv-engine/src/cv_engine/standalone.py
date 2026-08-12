"""Self-contained dashboard: one HTML file you can open anywhere.

Unlike ``pipeline._write_dashboard`` (which links to sibling files on disk),
this renderer **embeds** each tailored CV directly into a single HTML document:

* the PDF is inlined as a ``data:`` URI behind a real *Download PDF* button, and
* the HTML CV is inlined via an ``<iframe srcdoc>`` for instant preview.

The result needs no server, no file paths and no network — email it, host it,
or publish it as a private link and every CV previews and downloads in-browser.
"""

from __future__ import annotations

import base64
import datetime as _dt
import html
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle with pipeline
    from .pipeline import Application

# Real UK job-board searches for live inside-IR35 roles, used in the "apply now"
# panel so the page is useful even before an API key is configured.
LIVE_SEARCH_BOARDS = [
    ("CV-Library", "https://www.cv-library.co.uk/search-jobs?q={q}&perform_search=1"),
    ("Indeed UK", "https://uk.indeed.com/jobs?q={q}"),
    ("Totaljobs", "https://www.totaljobs.com/jobs/{qslug}"),
    ("RailwayPeople", "https://www.railwaypeople.com/jobs?search={q}"),
    ("LinkedIn", "https://www.linkedin.com/jobs/search/?keywords={q}"),
]


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def _b64_pdf(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _live_links(titles: list[str]) -> str:
    query = f"inside IR35 {titles[0]}" if titles else "inside IR35 project manager"
    q = query.replace(" ", "+")
    qslug = query.replace(" ", "-").lower()
    chips = "".join(
        f'<a class="chip" target="_blank" rel="noopener" '
        f'href="{_esc(url.format(q=q, qslug=qslug))}">{_esc(name)} &rarr;</a>'
        for name, url in LIVE_SEARCH_BOARDS
    )
    return chips


def render_standalone_dashboard(
    apps: list["Application"],
    out_path: str | Path,
    *,
    candidate_name: str = "",
    target_titles: list[str] | None = None,
    ir35_filter: str = "inside",
    live_data: bool = False,
    run_date: str | None = None,
) -> Path:
    date_str = run_date or _dt.date.today().isoformat()
    safe_name = (candidate_name or "candidate").replace(" ", "_")

    cards = []
    for app in apps:
        j = app.job
        b64 = _b64_pdf(app.pdf)
        cv_html = app.html.read_text(encoding="utf-8")
        pdf_name = f"{safe_name}_CV_{j.slug}.pdf"
        matched = ", ".join(j.matched_skills[:8])
        cards.append(f"""
      <article class="card">
        <div class="card-head">
          <div class="score" title="Match score">{j.match_score:.0f}</div>
          <div class="meta">
            <h3>{_esc(j.title)}</h3>
            <div class="sub">{_esc(j.company)} &middot; {_esc(j.location)}
              &middot; <span class="ir35 {_esc(j.ir35_status)}">{_esc(j.ir35_status)} IR35</span>
              {(' &middot; ' + _esc(j.day_rate)) if j.day_rate else ''}</div>
            {f'<div class="matched">Matched: {_esc(matched)}</div>' if matched else ''}
          </div>
        </div>
        <div class="actions">
          <a class="btn download" download="{_esc(pdf_name)}"
             href="data:application/pdf;base64,{b64}">&#8681; Download CV (PDF)</a>
          <button class="btn preview" onclick="togglePreview(this)">Preview CV</button>
          <a class="btn apply" target="_blank" rel="noopener" href="{_esc(j.url)}">Apply &rarr;</a>
        </div>
        <div class="preview-wrap" hidden>
          <iframe class="cv-frame" srcdoc="{_esc(cv_html)}" title="CV preview"></iframe>
        </div>
      </article>""")

    cards_html = "".join(cards) or (
        '<p class="empty">No roles matched today. Widen your search terms or '
        'lower <code>min_match_score</code> in config.yaml.</p>'
    )

    banner = (
        '<div class="note live">Showing <strong>live</strong> roles pulled from your '
        'configured job boards.</div>'
        if live_data else
        '<div class="note sample"><strong>These are sample roles</strong> showing the '
        'format. Your CVs below are <strong>real and tailored</strong> — preview and '
        'download them now. For <strong>live listings</strong>, add a free Adzuna or '
        'Reed API key (see the project README), or jump straight into live inside-IR35 '
        'searches:</div>'
    )

    doc = f"""<title>{_esc(candidate_name)} — Job Applications</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0;
          background: #0b1220; color: #e5e9f0; }}
  a {{ color: inherit; }}
  header {{ padding: 28px 24px 20px; background: linear-gradient(135deg,#111c33,#0b1220);
           border-bottom: 1px solid #1e2a44; }}
  h1 {{ margin: 0; font-size: 24px; }}
  .tagline {{ color: #93a2c0; margin-top: 6px; font-size: 14px; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 20px 16px 60px; }}
  .note {{ border-radius: 10px; padding: 14px 16px; font-size: 14px; margin: 16px 0;
           line-height: 1.5; }}
  .note.sample {{ background: #2a2313; border: 1px solid #6b5a1f; color: #f2e4b8; }}
  .note.live {{ background: #10281a; border: 1px solid #2f6b46; color: #bff0d0; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 20px; }}
  .chip {{ background: #16233d; border: 1px solid #274063; padding: 7px 12px;
           border-radius: 999px; font-size: 13px; text-decoration: none; font-weight: 600; }}
  .chip:hover {{ background: #1d2f52; }}
  .card {{ background: #101a2e; border: 1px solid #1e2a44; border-radius: 12px;
           padding: 16px; margin-bottom: 14px; }}
  .card-head {{ display: flex; gap: 14px; align-items: flex-start; }}
  .score {{ flex: 0 0 auto; width: 46px; height: 46px; border-radius: 10px;
            background: #0e2a3a; color: #38bdf8; font-weight: 800; font-size: 20px;
            display: flex; align-items: center; justify-content: center; }}
  .meta h3 {{ margin: 0 0 3px; font-size: 17px; }}
  .sub {{ color: #9fb0cf; font-size: 13px; }}
  .matched {{ color: #6f819f; font-size: 12px; margin-top: 4px; }}
  .ir35 {{ padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
  .ir35.inside {{ background: #7f1d1d; color: #fecaca; }}
  .ir35.outside {{ background: #14532d; color: #bbf7d0; }}
  .ir35.unknown {{ background: #334155; color: #cbd5e1; }}
  .actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }}
  .btn {{ font: inherit; font-size: 13px; font-weight: 700; padding: 9px 14px;
          border-radius: 8px; border: 0; cursor: pointer; text-decoration: none;
          display: inline-block; }}
  .btn.download {{ background: #38bdf8; color: #08131f; }}
  .btn.preview {{ background: #1e2a44; color: #e5e9f0; }}
  .btn.apply {{ background: #22c55e; color: #06210f; }}
  .btn:hover {{ opacity: .9; }}
  .preview-wrap {{ margin-top: 14px; }}
  .cv-frame {{ width: 100%; height: 640px; border: 1px solid #24314f; border-radius: 8px;
               background: #fff; }}
  .empty {{ color: #93a2c0; text-align: center; padding: 40px 0; }}
  footer {{ color: #6f819f; font-size: 12px; text-align: center; padding: 24px; }}
</style>
<header>
  <h1>{_esc(candidate_name)} — Tailored Job Applications</h1>
  <div class="tagline">{date_str} &middot; {len(apps)} tailored CV(s) &middot;
    IR35 filter: <strong>{_esc(ir35_filter)}</strong> &middot; sorted by match score</div>
</header>
<main>
  {banner}
  {'' if live_data else f'<div class="chips">{_live_links(target_titles or [])}</div>'}
  {cards_html}
  <footer>Generated by the CV Application Engine. Each CV is tailored per role
    (skills &amp; summary re-prioritised to match the advert).</footer>
</main>
<script>
  function togglePreview(btn) {{
    var wrap = btn.closest('.card').querySelector('.preview-wrap');
    wrap.hidden = !wrap.hidden;
    btn.textContent = wrap.hidden ? 'Preview CV' : 'Hide preview';
  }}
</script>"""

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return out
