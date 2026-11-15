"""England-wide coverage: geography, Land Registry data, county searches, and place filters."""

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import geo
from src.config import config_for_outcode, load_config
from src.storage import Storage

# --------------------------------------------------------------------------- geography


@pytest.mark.parametrize("oc, region", [
    ("ME4", "South East"), ("M14", "North West"), ("LS6", "Yorkshire and the Humber"), ("B12", "West Midlands"),
    ("NE6", "North East"), ("SW1A", "London"), ("EX4", "South West"), ("NG7", "East Midlands"),
    ("CB1", "East of England"), ("CF10", "Wales"), ("EH1", "Scotland"),
])
def test_region_of(oc, region):
    assert geo.region_of(oc) == region


def test_parse_postcode_and_coverage():
    assert geo.parse_postcode("22 Test Grove, Manchester M14 5AB") == ("M14 5AB", "M14")
    assert geo.parse_postcode("Flat 1, London SW1A 1AA") == ("SW1A 1AA", "SW1A")
    assert geo.parse_postcode("High Street, Chatham ME4") == ("", "ME4")
    assert geo.parse_postcode("Mock Mill, Salford") == ("", "")
    assert geo.in_england("M14") and not geo.in_england("CF10") and not geo.in_england("EH1")


def test_matches_place():
    assert geo.matches_place("M14", "M14 5AB", prefixes=["M"])
    assert not geo.matches_place("ME4", "ME4 6XX", prefixes=["M"])  # area letters must match exactly
    assert geo.matches_place("ME4", "ME4 6XX", prefixes=["ME4 6"])
    assert not geo.matches_place("ME4", "ME4 5QA", prefixes=["ME4 6"])
    assert geo.matches_place("LS6", "", regions=["Yorkshire and the Humber"])
    assert not geo.matches_place("LS6", "", regions=["London"])


def test_region_overrides_apply():
    cfg = load_config()
    cfg["region_overrides"] = {"London": {"thresholds": {"min_gross_yield": 0.05}}}
    assert config_for_outcode(cfg, "SE15")["thresholds"]["min_gross_yield"] == 0.05
    assert config_for_outcode(cfg, "ME4")["thresholds"]["min_gross_yield"] == 0.068


# --------------------------------------------------------------------------- Land Registry

def _ppd_line(tid, price, d, pc, t="T", new="N", cat="A", status="A"):
    return f'"{{{tid}}}","{price}","{d} 00:00","{pc}","{t}","{new}","F","1","","High St","","Town","District","County","{cat}","{status}"'


def test_ppd_apply_rows_add_change_delete():
    from src.ppd import apply_rows, parse_csv_text, sold_prices

    st = Storage(":memory:")
    today = time.strftime("%Y-%m-%d")
    rows = "\n".join([
        _ppd_line("A1", 150000, today, "M14 5AB"),
        _ppd_line("A2", 160000, today, "M14 6CD"),
        _ppd_line("A3", 170000, today, "M14 7EF"),
        _ppd_line("A4", 400000, today, "M14 7EF", t="D"),
        _ppd_line("A5", 99000, today, "M14 7EF", cat="B"),         # repossession etc: skipped
        _ppd_line("A6", 250000, today, "M14 7EF", new="Y"),        # new build: excluded from comps
    ])
    c = apply_rows(st, parse_csv_text(rows))
    assert c == {"added": 5, "deleted": 0, "skipped": 1}
    assert sorted(sold_prices(st, "M14", "terraced")) == [150000, 160000, 170000]
    assert sold_prices(st, "M14", "detached") == [400000]
    # monthly update: change A1's price, delete A2
    upd = "\n".join([_ppd_line("A1", 155000, today, "M14 5AB", status="C"),
                     _ppd_line("A2", 160000, today, "M14 6CD", status="D")])
    c2 = apply_rows(st, parse_csv_text(upd))
    assert c2["deleted"] == 1
    assert sorted(sold_prices(st, "M14", "terraced")) == [155000, 170000]


def test_ppd_refresh_downloads_yearly_then_monthly(tmp_path):
    from src.ppd import refresh, summary

    today = time.strftime("%Y-%m-%d")
    year = int(today[:4])
    files = {
        f"/pp-{year}.csv": "\n".join(_ppd_line(f"Y{i}", 100000 + i * 1000, today, "LS6 2AA") for i in range(5)),
        f"/pp-{year - 1}.csv": _ppd_line("P1", 90000, today, "LS6 3BB"),
        "/pp-monthly-update-new-version.csv": _ppd_line("Y0", 0, today, "LS6 2AA", status="D"),
    }

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            body = files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            self.wfile.write((body or "").encode())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = load_config()
        cfg["land_registry_ppd"] = {**cfg["land_registry_ppd"], "base_url": f"http://127.0.0.1:{srv.server_address[1]}"}
        st = Storage(":memory:")
        r1 = refresh(st, cfg)
        assert r1["added"] == 6 and summary(st)["rows"] == 6
        assert refresh(st, cfg)["skipped"] == "fresh"
        r2 = refresh(st, cfg, force=True)  # table not empty -> monthly file only
        assert r2["deleted"] == 1 and summary(st)["rows"] == 5
    finally:
        srv.shutdown()


# --------------------------------------------------------------------------- county search end to end


def _browser_ok() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("BRRR_CHROMIUM_PATH")) or Path("/opt/pw-browsers/chromium").exists()


@pytest.mark.skipif(not _browser_ok(), reason="needs Playwright + a Chromium build")
def test_county_search_locates_filters_and_fetches_comps(monkeypatch):
    if not os.environ.get("BRRR_CHROMIUM_PATH") and Path("/opt/pw-browsers/chromium").exists():
        monkeypatch.setenv("BRRR_CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    from src.portals.collector import PortalSession
    from tests.portal_mock import Handler, portal_config, start

    srv, base = start()
    try:
        cfg = load_config()
        cfg["portals"] = {**portal_config(base), "enabled": ["rightmove"]}
        cfg["land_registry_ppd"] = {**cfg["land_registry_ppd"], "postcodes_io": f"{base}/pio"}
        cfg["coverage"] = {**cfg["coverage"], "max_pages_per_search": 2}
        st = Storage(":memory:")
        with PortalSession(cfg, st) as s:
            listings = s.collect_areas(["Greater Manchester"])
            by_id = {x.listing_id: x for x in listings}
            assert set(by_id) == {"rm-92001", "rm-92002", "rm-93001"}  # Cardiff listing dropped (Wales)
            assert by_id["rm-92001"].outcode == "M14" and by_id["rm-92001"].region == "North West"
            assert by_id["rm-92002"].outcode == "M5"  # placed from its map position via postcodes.io
            assert by_id["rm-93001"].category.value == "commercial" and by_id["rm-93001"].region.startswith("York")
            assert s.located == {"kept": 3, "no_postcode": 0, "outside_coverage": 1}
            assert st.kv_get("rightmove_locid:Greater Manchester") == "REGION^79192"
            # on-demand comps for the M14 terrace: one rent page and one sale page for that district
            Handler.district_rent = True
            assert s.ensure_comps(by_id["rm-92001"]) is True
            assert len(st.market_prices("M14", "rent", None, 3, "2000-01-01")) >= 3
            assert s.ensure_comps(by_id["rm-92001"]) is False  # cached now
    finally:
        Handler.district_rent = False
        srv.shutdown()


# --------------------------------------------------------------------------- place filters in the API


def test_api_place_filters(tmp_path, monkeypatch):
    from src import config as config_mod

    monkeypatch.setenv("BRRR_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config_mod, "USER_OVERRIDES", tmp_path / "user.yaml")
    from src import web
    c = TestClient(web.create_app())
    c.post("/api/auth/setup", json={"email": "o@example.com", "password": "correct-horse", "name": "O"})
    c.post("/api/scan", json={"sources": ["sample"], "use_claude": False})
    for _ in range(300):
        j = c.get("/api/job").json()
        if j and not j["running"]:
            break
        time.sleep(0.03)
    # add a North West deal by copying a sample deal into M14
    st = Storage(str(tmp_path / "t.db"))
    d = st.query_deals()[0]
    d2 = d.model_copy(deep=True)
    d2.listing.listing_id = d2.result.listing_id = "nw-1"
    d2.listing.outcode, d2.listing.postcode = "M14", "M14 5AB"
    st.upsert_listing(d2.listing)
    st.save_deal(d2)

    def ids(**params):
        return {x["listing_id"] for x in c.get("/api/deals", params={"include_ineligible": True, **params}).json()}

    assert ids(region="North West") == {"nw-1"}
    assert ids(postcode="M") == {"nw-1"}                       # M, not ME
    assert ids(postcode="ME4") and "nw-1" not in ids(postcode="ME4")
    assert ids(area="CT") and all(x.startswith("pd-") for x in ids(area="CT"))
    assert ids(postcode="M14 5, CT9") >= {"nw-1"} and len(ids(postcode="M14 5, CT9")) >= 2
    tree = c.get("/api/geo").json()["tree"]
    assert {r["region"] for r in tree} >= {"South East", "North West"}
    summary = c.get("/api/summary").json()
    assert {r["outcode"] for r in summary} >= {"ME", "CT", "M"}
    # a person's region preference narrows their digest
    c.put("/api/auth/me", json={"prefs": {"regions": ["North West"], "categories": []}})
    dg = c.get("/api/digest").json()
    assert {x["region"] for x in dg["best"] + dg["near_misses"]} <= {"North West"}
    cov = c.get("/api/coverage").json()
    assert cov["scope"] == "england" and len(cov["all_areas"]) == 47


# --------------------------------------------------------------------------- reduced / stale / sweep searches


@pytest.mark.skipif(not _browser_ok(), reason="needs Playwright + a Chromium build")
def test_reduced_stale_sweep_searches(monkeypatch):
    if not os.environ.get("BRRR_CHROMIUM_PATH") and Path("/opt/pw-browsers/chromium").exists():
        monkeypatch.setenv("BRRR_CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    from src.portals.collector import PortalSession
    from tests.portal_mock import portal_config, start

    srv, base = start()
    try:
        cfg = load_config()
        cfg["portals"] = {**portal_config(base), "enabled": ["rightmove"]}
        cfg["coverage"] = {**cfg["coverage"], "searches": {
            "reduced": {"enabled": True, "pages": 1, "within_days": 2, "portals": ["rightmove"]},
            "stale": {"enabled": True, "pages": 1, "min_days_on_market": 90, "cycle_nights": 1, "portals": ["rightmove"]},
            "sweep": {"enabled": True, "pages_per_search": 1, "cycle_nights": 1, "portals": ["rightmove"],
                      "keywords": ["renovation"]},
        }}
        st = Storage(":memory:")
        with PortalSession(cfg, st) as s:
            got = {x.listing_id: x for x in s.collect_extra(["Greater Manchester"], ["reduced", "stale", "sweep"])}
        assert set(got) == {"rm-95001", "rm-96001", "rm-97001"}
        assert got["rm-95001"].search_kind == "reduced" and got["rm-95001"].reduced_on
        assert got["rm-96001"].search_kind == "stale" and got["rm-96001"].days_on_market >= 199
        assert got["rm-97001"].search_kind == "sweep" and got["rm-97001"].listed_on
        # everything the extra searches saw also feeds local asking-price comps
        assert len(st.market_prices("M14", "sale", None, None, "2000-01-01")) == 5
    finally:
        srv.shutdown()


def test_unchanged_relisting_is_skipped_and_counted():
    from src.http import FixtureClient
    from src.main import RunSummary, process_listing

    st = Storage(":memory:")
    from src.main import run_scan

    run_scan(sources=["sample"], storage=st, dry_run=True, use_claude=False)
    lst = st.get_deal("pd-1001").listing.model_copy(update={"search_kind": "sweep"})
    summary = RunSummary(outcodes=["ME4"])
    out = process_listing(lst, client=FixtureClient(), cfg=load_config(), storage=st, analyst=None,
                          summary=summary, use_external_fallbacks=False)
    assert out is None and summary.unchanged_skipped == 1
    f = summary.as_dict()["funnel"]
    assert f["total"]["seen"] == 1 and f["total"]["unchanged"] == 1 and f["by_search"]["sweep"]["unchanged"] == 1
    # a price cut makes it count again
    cut = lst.model_copy(update={"asking_price": lst.asking_price - 5000})
    assert process_listing(cut, client=FixtureClient(), cfg=load_config(), storage=st, analyst=None,
                           summary=summary, use_external_fallbacks=False) is not None


def test_sample_run_funnel():
    from src.main import run_scan

    s = run_scan(sources=["sample"], storage=Storage(":memory:"), dry_run=True, use_claude=False).as_dict()
    t = s["funnel"]["total"]
    assert t["seen"] == 17 and t["excluded"] == 5 and t["checked"] == 12 and t["passed"] == 3
    assert t["possible"] >= t["passed"] and t["worth_offer"] >= 1
    assert s["funnel"]["by_region"]["South East"]["seen"] == 17
    assert any(k.startswith("Fails:") for k in s["funnel"]["reasons"])


def test_rotation_never_leaves_small_coverage_empty():
    from datetime import date, timedelta

    from src.portals.collector import areas_tonight

    start = date(2026, 11, 15)
    for i in range(7):  # Kent only: searched every night, not one night in seven
        assert areas_tonight(["Kent"], 7, start + timedelta(days=i)) == ["Kent"]
    three = ["Essex", "Kent", "Surrey"]
    seen = set()
    for i in range(3):
        tonight = areas_tonight(three, 7, start + timedelta(days=i))
        assert len(tonight) == 1
        seen |= set(tonight)
    assert seen == set(three)
    counties = [f"C{i:02d}" for i in range(47)]
    week = [areas_tonight(counties, 7, start + timedelta(days=i)) for i in range(7)]
    assert sorted(sum(week, [])) == counties


class _FakeStorage:
    def __init__(self, rents):  # {beds: [prices]}
        self.rents = rents

    def market_prices(self, oc, channel, ptype, beds, since):
        return list(self.rents.get(beds, [])) if channel == "rent" and ptype is None else []


def test_rent_fallbacks_stay_conservative():
    from src.local_comps import LocalMarketClient

    cfg = {"local_comps": {"min_samples": 3}}
    # 3-bed thin, 2-bed plentiful: borrow 2-bed rent unadjusted
    c = LocalMarketClient(_FakeStorage({3: [1400], 2: [1000, 1100, 1200]}), cfg)
    r = c.get("rents", postcode="TN24", type="terraced_house", bedrooms=3)
    assert r["data"]["long_let"]["average"] == 1100
    # studio with no studios: 1-bed at 80%
    c = LocalMarketClient(_FakeStorage({1: [800, 900, 1000]}), cfg)
    r = c.get("rents", postcode="CT1", type="flat", bedrooms=0)
    assert r["data"]["long_let"]["average"] == 720
