"""Offline integration tests: ingestion parsing, eligibility, storage, pipeline, web API."""

from pathlib import Path

import pytest

from src.claude_analyst import heuristic_assessment
from src.config import load_config
from src.ingestion import check_eligibility, infer_tenure, normalise_type, parse_rss
from src.main import run_scan
from src.models import PropertyCategory, RawListing, Tenure
from src.notifier import format_memo, md2
from src.storage import Storage

FIX = Path(__file__).parent / "fixtures"
CFG = load_config()


def _listing(**kw) -> RawListing:
    base = dict(listing_id="x", source="t", postcode="ME4 1AA", outcode="ME4", asking_price=100_000,
                property_type="terraced", tenure=Tenure.FREEHOLD, description="")
    return RawListing(**{**base, **kw})


@pytest.mark.parametrize("raw, expected", [
    ("terraced_house", "terraced"), ("Semi-detached house", "semi-detached"),
    ("End of terrace", "end-terrace"), ("Flat", "flat"), ("detached_house", "detached"),
    ("Retail shop", "commercial"), ("mixed-use", "mixed-use"), (None, "unknown"),
])
def test_normalise_type(raw, expected):
    assert normalise_type(raw) == expected


def test_infer_tenure():
    assert infer_tenure("Leasehold, 112 years remaining") == (Tenure.LEASEHOLD, 112)
    assert infer_tenure("Share of freehold flat") == (Tenure.SHARE_OF_FREEHOLD, None)
    assert infer_tenure("Freehold house") == (Tenure.FREEHOLD, None)
    assert infer_tenure("lovely home")[0] == Tenure.UNKNOWN


def test_eligibility_rules():
    assert check_eligibility(_listing(), CFG) == (True, [])
    ok, why = check_eligibility(_listing(property_type="detached"), CFG)
    assert not ok and "detached" in why[0]
    ok, _ = check_eligibility(_listing(property_type="flat", tenure=Tenure.LEASEHOLD,
                                       lease_years_remaining=90), CFG)
    assert not ok  # must be > 90
    ok, _ = check_eligibility(_listing(property_type="flat", tenure=Tenure.LEASEHOLD,
                                       lease_years_remaining=91), CFG)
    assert ok
    ok, _ = check_eligibility(_listing(property_type="flat", tenure=Tenure.LEASEHOLD), CFG)
    assert not ok
    ok, _ = check_eligibility(_listing(property_type="flat", tenure=Tenure.LEASEHOLD), CFG,
                              allow_unknown_lease=True)
    assert ok
    ok, _ = check_eligibility(_listing(description="Shared ownership 40% share"), CFG)
    assert not ok
    ok, _ = check_eligibility(_listing(property_type="commercial",
                                       category=PropertyCategory.COMMERCIAL), CFG)
    assert ok  # include_commercial: true


def test_parse_rss_filters_outcodes():
    xml = (FIX / "auction_feed.xml").read_text()
    out = parse_rss(xml, "sample", {"ME4", "CT9"})
    assert len(out) == 1
    lot = out[0]
    assert lot.outcode == "ME4" and lot.asking_price == 95_000 and lot.is_auction
    assert lot.tenure == Tenure.FREEHOLD and lot.bedrooms == 2


def test_heuristic_assessment_tiers():
    assert heuristic_assessment(_listing(description="needs modernisation")).tier == "medium"
    assert heuristic_assessment(_listing(description="complete renovation required")).tier == "heavy"
    assert heuristic_assessment(_listing(description="subsidence noted")).tier == "structural"
    a = heuristic_assessment(_listing(description="Retirement flat, 99 years remaining"))
    assert a.is_excluded_type and a.detected_lease_years == 99


def test_md2_escaping():
    assert md2("£1,000.50 (est)") == r"£1,000\.50 \(est\)"


def test_offline_pipeline_end_to_end(capsys):
    storage = Storage(":memory:")
    s = run_scan(offline=True, dry_run=True, use_claude=False, storage=storage)
    assert s.fetched == 17
    assert s.pre_screen_rejected == 5  # detached, short lease, retirement, shared ownership, park home
    assert s.underwritten == 12
    assert s.passed >= 1 and not s.errors
    deals = storage.query_deals(passed_only=True)
    assert deals and all(d.result.passed and d.result.eligible for d in deals)
    assert "BRRR deal" in capsys.readouterr().out
    # Commercial filter
    com = storage.query_deals(categories=["commercial", "mixed-use"])
    assert {d.listing.category for d in com} <= {PropertyCategory.COMMERCIAL, PropertyCategory.MIXED_USE}
    assert storage.outcode_summary()
    assert "##" not in format_memo(deals[0])


def test_price_drop_tracking():
    st = Storage(":memory:")
    lst = _listing(listing_id="drop1", asking_price=120_000)
    assert st.upsert_listing(lst)["is_new"]
    info = st.upsert_listing(lst.model_copy(update={"asking_price": 108_000}))
    assert info["price_changed"] and info["drop_pct"] == 10.0
    assert [h["price"] for h in st.price_history("drop1")] == [120_000, 108_000]


def _wait_job(c):
    import time
    for _ in range(200):
        j = c.get("/api/job").json()
        if j and not j["running"]:
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_web_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src import config as config_mod

    monkeypatch.setenv("BRRR_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config_mod, "USER_OVERRIDES", tmp_path / "user.yaml")
    monkeypatch.setattr(config_mod, "ENV_PATH", tmp_path / ".env")
    from src import web
    c = TestClient(web.create_app())

    assert c.get("/api/meta").status_code == 401  # signed out
    assert c.get("/api/auth/status").json()["setup_needed"] is True
    r = c.post("/api/auth/setup", json={"email": "me@example.com", "password": "correct-horse", "name": "Me"})
    assert r.status_code == 200 and r.json()["user"]["is_admin"]
    assert c.get("/api/meta").json()["deal_count"] == 0
    assert c.post("/api/scan", json={"sources": ["sample"], "use_claude": False}).status_code == 200
    job = _wait_job(c)
    assert job["error"] is None and job["result"]["underwritten"] == 12, job

    rows = c.get("/api/deals", params={"outcode": "ME4"}).json()
    assert rows and all(d["outcode"] == "ME4" for d in rows)
    com = c.get("/api/deals", params={"category": "commercial"}).json()
    assert all(d["category"] == "commercial" for d in com)
    assert c.get("/").status_code == 200
    lid = rows[0]["listing_id"]
    detail = c.get(f"/api/deals/{lid}").json()
    assert detail["listing"]["listing_id"] == lid and detail["pipeline"]["status"] == "new"

    # pipeline status + notes, and filtering on them
    c.put(f"/api/deals/{lid}/status", json={"status": "shortlisted", "notes": "call agent"})
    sl = c.get("/api/deals", params={"status": "shortlisted"}).json()
    assert [d["listing_id"] for d in sl] == [lid] and sl[0]["notes"] == "call agent"
    c.put(f"/api/deals/{lid}/status", json={"status": "rejected"})
    assert lid not in {d["listing_id"] for d in c.get("/api/deals", params={"hide_rejected": True}).json()}

    # what-if: offering the max offer must clear gate 1
    w = c.post(f"/api/deals/{lid}/whatif", json={"asking_price": detail["result"]["max_offer_price"]}).json()
    assert w["gates"][0]["passed"] and set(w["tier_costs"]) == {"light", "medium", "heavy", "structural"}

    csv_text = c.get("/api/deals.csv").text
    assert csv_text.startswith("listing_id,status,passed") and lid in csv_text

    # settings: thresholds saved to the overlay file, secrets to .env (masked on read)
    r = c.put("/api/settings", json={"secrets": {"TELEGRAM_CHAT_ID": "123456789"},
                                     "config": {"thresholds": {"min_gross_yield": 0.07},
                                                "not_allowed": {"x": 1}}}).json()
    assert r["saved_secrets"] == ["TELEGRAM_CHAT_ID"] and "not_allowed" not in r["saved_config"]
    s = c.get("/api/settings").json()
    assert s["config"]["thresholds"]["min_gross_yield"] == 0.07
    assert s["secrets"]["TELEGRAM_CHAT_ID"] == {"set": True, "hint": "…6789"}
    assert "123456789" in (tmp_path / ".env").read_text()

    c.post("/api/rescore")
    assert _wait_job(c)["result"]["rescored"] == 12
    assert c.get("/api/runs").json()[0]["status"] == "ok"
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
