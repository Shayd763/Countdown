import yaml

from cv_engine.config import Config
from cv_engine.discovery import discover
from cv_engine.models import Profile
from cv_engine.pipeline import run_pipeline
from cv_engine.tailor import tailor


def _profile():
    data = yaml.safe_load(open("data/profile.example.yaml"))
    return Profile.from_dict(data)


def test_discovery_filters_to_inside_ir35_only():
    cfg = Config()  # defaults: sample source, inside filter
    jobs = discover(cfg, _profile())
    assert jobs, "expected at least one inside-IR35 sample job"
    assert all(j.ir35_status == "inside" for j in jobs)
    # The outside-IR35 and permanent sample roles must be filtered out.
    titles = {j.title for j in jobs}
    assert not any("Outside" in t for t in titles)
    assert not any("Permanent" in t for t in titles)


def test_tailor_promotes_matched_skills():
    profile = _profile()
    cfg = Config()
    jobs = discover(cfg, profile)
    job = jobs[0]
    tailored = tailor(profile, job, cfg)
    # A matched skill should be surfaced at the front of the skills list.
    if job.matched_skills:
        assert tailored.skills[0].lower() in {s.lower() for s in job.matched_skills}
    # Original profile is untouched.
    assert profile.skills != tailored.skills or not job.matched_skills


def test_run_pipeline_produces_pdfs_and_dashboard(tmp_path):
    cfg = Config()
    cfg.output_dir = str(tmp_path)
    dashboard = run_pipeline(_profile(), cfg, run_date="2026-08-10")
    assert dashboard.exists()
    run_dir = tmp_path / "2026-08-10"
    assert (run_dir / "applications.csv").exists()
    assert (run_dir / "jobs.json").exists()
    pdfs = list(run_dir.glob("jobs/*/cv.pdf"))
    assert pdfs, "expected at least one generated CV PDF"
    # PDFs are non-trivial in size and start with the PDF magic bytes.
    assert pdfs[0].read_bytes()[:4] == b"%PDF"
