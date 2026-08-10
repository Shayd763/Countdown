# CV Application Engine

A daily engine that turns **one master profile** into a stream of **click-to-apply
job applications**. Every day it:

1. **Discovers** UK contract roles from job boards.
2. **Filters to inside-IR35** roles (configurable).
3. **Tailors your CV** to each role and renders it in the **Harvard résumé
   format** as a print-ready **PDF** (plus an HTML preview and a cover note).
4. Builds a **dashboard** — one table with a *Preview*, *Download PDF* and
   *Apply →* button per role — so you can open each advert, upload the matching
   CV and apply, en masse.

> It stops short of *auto-submitting* applications on your behalf: most job
> boards forbid automated submission, and each employer's form is different.
> The engine gets you to a one-click-per-role finish line with the right CV
> already generated.

---

## Quick start (no API keys, no network)

```bash
cd cv-engine
pip install -r requirements.txt        # PyYAML, requests, fpdf2

python main.py init                    # writes config.yaml + data/profile.yaml
python main.py run                     # generates CVs from the offline samples

open output/<today>/dashboard.html     # macOS (use xdg-open on Linux)
```

The first run uses a bundled **sample** job source so you can see the whole
flow immediately. Then make it yours:

1. Edit **`data/profile.yaml`** — your name, summary, skills, experience,
   education, certifications. This is the "database of your CV and experience".
2. Add a real job source in **`config.yaml`** (see below).
3. Re-run `python main.py run`.

---

## Commands

| Command | What it does |
| --- | --- |
| `python main.py init` | Scaffold `config.yaml` and `data/profile.yaml` from the examples. |
| `python main.py validate` | Load and sanity-check your profile. |
| `python main.py discover` | Find + print matching roles (no CV generation). |
| `python main.py run` | Full pipeline → dated folder with dashboard, PDFs, CSV. |

Add `-v` for verbose logging. Installed as a package it's also available as the
`cv-engine` command (`pip install -e .`).

---

## Live job sources

Sources are enabled in `config.yaml` under `sources:`. Secrets are read from
environment variables (preferred) or the config file.

### Adzuna (recommended, free)
1. Register at <https://developer.adzuna.com/> for an `app_id` + `app_key`.
2. `export ADZUNA_APP_ID=... ADZUNA_APP_KEY=...`
3. Add `adzuna` to `sources:`.

### Reed (free)
1. Get an API key at <https://www.reed.co.uk/developers>.
2. `export REED_API_KEY=...`
3. Add `reed` to `sources:`.

Multiple sources are merged and de-duplicated automatically. Both fetch the
full advert text so the **IR35 classifier** can read phrases like
"inside IR35", "outside IR35", "umbrella only" or "PAYE only".

---

## How tailoring works

For each matched role the engine (deterministically, offline):

- **Re-orders your skills** so the ones the advert asks for appear first.
- **Re-orders each role's bullets** to surface the most relevant achievements.
- **Rewrites your summary** to name the role and lead with matched strengths.

**Optional richer tailoring** via the Claude API: set `use_llm: true` in
`config.yaml` and `export ANTHROPIC_API_KEY=...`. The model rewrites the summary
and picks the strongest bullets per job. It **degrades gracefully** — any error
falls straight back to the deterministic path, so a daily run never breaks
because of the model.

---

## The Harvard format

The PDF follows the Harvard FAS/OCS résumé conventions: a centered name and
contact line, small-caps section headings under full-width rules, reverse-
chronological entries with right-aligned dates, and concise action-verb
bullets. Section order is configurable (`section_order` in `config.yaml`) —
lead with `experience` (default, best for contractors) or `education` for the
classic academic layout.

Rendering uses **fpdf2** — pure Python, so there are no system libraries or
headless browsers to install, and the PDFs upload cleanly to application forms.

---

## Output layout

```
output/
  index.html                 # links to every dated run
  2026-08-10/
    dashboard.html           # the click-to-apply table
    applications.csv         # machine-readable summary
    jobs.json                # full manifest
    jobs/
      <id>-<role-slug>/
        cv.pdf               # tailored Harvard CV (upload this)
        cv.html              # preview
        cover_note.md        # editable cover note
        job.json             # the advert + analysis
```

---

## Run it daily, automatically

A GitHub Actions workflow (`.github/workflows/cv-engine-daily.yml`) runs the
engine every morning and uploads the whole `output/` folder as a downloadable
artifact. To make it use live boards, add repository **secrets**:
`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `REED_API_KEY`, and optionally
`ANTHROPIC_API_KEY`.

Commit your real `data/profile.yaml` (or the workflow falls back to the example
one). Locally you can instead schedule `python main.py run` with `cron`.

---

## Configuration reference (`config.yaml`)

| Key | Meaning |
| --- | --- |
| `sources` | Which adapters to use: `sample`, `adzuna`, `reed`. |
| `search_terms` | Keywords sent to the boards. |
| `locations` | `UK`, or specific towns/cities. |
| `max_days_old` | Ignore adverts older than this. |
| `ir35_filter` | `inside` (default), `outside`, or `any`. |
| `min_match_score` | Drop roles scoring below this (0–100). |
| `max_results` | Cap applications per run. |
| `section_order` | Order of CV sections. |
| `make_cover_note` | Emit a cover note per role. |
| `use_llm` / `llm_model` | Opt-in Claude-tailored summaries. |

---

## Development

```bash
cd cv-engine
pip install -r requirements.txt pytest
python -m pytest -q          # 11 tests: IR35 classifier, matching, pipeline
```

Structure: `src/cv_engine/` holds the package — `models`, `config`,
`profile_store`, `ir35`, `matching`, `discovery`, `sources/`, `tailor`,
`render`, `cover`, `pipeline`, `cli`.
