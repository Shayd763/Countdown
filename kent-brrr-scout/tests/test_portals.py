"""Portal collector: pure extraction tests + a real-browser run against the local mock portals."""

import os
import re
from pathlib import Path

import pytest

from src.portals.extract import (
    detect_block,
    extract_detail,
    extract_listings,
    json_blobs,
    parse_card_text,
    parse_money,
)

RM_RE = re.compile(r"/properties/(\d+)")


@pytest.mark.parametrize("raw, expected", [
    (150000, (150000.0, None)),
    ("£150,000", (150000.0, None)),
    ("Guide price £95,000+", (95000.0, None)),
    ("£1,250 pcm", (1250.0, "monthly")),
    ("£288 pw", (288.0, "weekly")),
    ({"amount": 400, "frequency": "weekly"}, (400.0, "weekly")),
    ({"displayPrices": [{"displayPrice": "£210,000"}]}, (210000.0, None)),
    (None, (None, None)),
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


def test_detect_block():
    assert detect_block(403, "x", "") == "HTTP 403"
    assert detect_block(200, "Just a moment...", "")
    assert detect_block(200, "Homes for sale", "3 bed terrace £120,000") is None


def test_extract_from_next_data_and_cards():
    html = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"searchResults":'
            '{"properties":[{"id":1,"bedrooms":3,"displayAddress":"1 A St, Chatham ME4 1AA",'
            '"propertySubType":"Semi-Detached","price":{"amount":150000},"propertyUrl":"/properties/1"},'
            '{"id":2,"bedrooms":2,"displayAddress":"Featured","price":{"amount":1},"propertyUrl":"/x"}]}}}}'
            '</script>')
    cards = [{"href": "/properties/1", "text": "£150,000\n3 bed semi"},
             {"href": "/properties/3", "text": "£99,000\n2 bed terraced house\n9 B Road, Chatham"}]
    out = {d["id"]: d for d in extract_listings(html, cards, base_url="https://rm.test",
                                                detail_re=RM_RE, channel="sale")}
    assert set(out) == {"1", "3"}  # "2" isn't a linked listing → dropped
    assert out["1"]["property_type"] == "semi-detached" and out["1"]["url"] == "https://rm.test/properties/1"
    assert out["3"]["price"] == 99000 and out["3"]["bedrooms"] == 2 and "B Road" in out["3"]["address"]


def test_weekly_rent_converted_to_monthly():
    d = parse_card_text("£300 pw\n2 bed flat\n1 C Ct, Margate", "u", "9", "rent")
    assert d["price"] == pytest.approx(1300.0)


def test_json_ld_and_flight():
    html = ('<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"item":'
            '{"@type":"House","name":"3 bed house","url":"https://z.test/for-sale/details/123456/",'
            '"offers":{"price":"175000"},"address":{"streetAddress":"5 D Rd","addressLocality":"Ramsgate"}}}]}'
            '</script><script>self.__next_f.push([1,"{\\"listingId\\":\\"777777\\",\\"price\\":\\"£80,000\\",'
            '\\"title\\":\\"2 bed flat for sale\\",\\"address\\":\\"Flat 1, Margate CT9\\"}"])</script>')
    blobs = json_blobs(html)
    assert len(blobs) == 2
    out = {d["id"]: d for d in extract_listings(html, [], base_url="https://z.test",
                                                detail_re=re.compile(r"/details/(\d+)"), channel="sale")}
    assert out["123456"]["price"] == 175000 and out["777777"]["property_type"] == "flat"


def test_extract_detail():
    html = ('<script>window.PAGE_MODEL = {"propertyData":{"text":{"description":"<p>Lovely terrace. '
            'Leasehold with 105 years remaining. 72 sq m.</p>"},"keyFeatures":["Garden"],'
            '"floorplans":[{"url":"//img/plan.png"}],"images":[{"url":"https://img/1.jpg"}]}}</script>')
    d = extract_detail(html, "")
    assert d["tenure"] == "leasehold" and d["lease_years"] == 105
    assert d["floor_area_sqft"] == pytest.approx(775.0, abs=1)
    assert d["floorplans"] == ["https://img/plan.png"] and d["images"] == ["https://img/1.jpg"]
    assert "• Garden" in d["description"]


def _browser_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("BRRR_CHROMIUM_PATH")) or Path("/opt/pw-browsers/chromium").exists()


@pytest.mark.skipif(not _browser_available(), reason="needs Playwright + a Chromium build")
def test_collector_against_mock_portals(monkeypatch):
    if not os.environ.get("BRRR_CHROMIUM_PATH") and Path("/opt/pw-browsers/chromium").exists():
        monkeypatch.setenv("BRRR_CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    from src.config import load_config
    from src.portals.collector import PortalSession
    from src.storage import Storage
    from tests.portal_mock import portal_config, start

    srv, base = start()
    try:
        cfg = load_config()
        cfg["portals"] = portal_config(base)
        st = Storage(":memory:")
        with PortalSession(cfg, st) as s:
            listings = s.collect(["ME4"])
            ids = {x.listing_id for x in listings}
            # Rightmove 3 + Zoopla 2, minus the Zoopla duplicate of rm-90001
            assert ids == {"rm-90001", "rm-90002", "rm-90003", "zp-70001"}
            assert s.status["onthemarket"]["state"] == "blocked"
            assert s.status["rightmove"]["state"] == "ok" and s.status["rightmove"]["rentals"] == 7
            assert s.status["zoopla"]["rentals"] == 3
            rm1 = next(x for x in listings if x.listing_id == "rm-90001")
            assert rm1.postcode == "ME4 6XX" and rm1.property_type == "terraced" and rm1.bedrooms == 3
            detailed = s.fetch_detail(rm1)
            assert "partial rewire" in detailed.description and detailed.floor_area_sqft == 915
            assert detailed.tenure.value == "freehold" and detailed.floorplan_urls
        rents = st.market_prices("ME4", "rent", "terraced", 3, "2000-01-01")
        assert sorted(rents)[:2] == [1500, 1550]
        assert any(abs(r - 1625.0) < 0.01 for r in rents)  # £375 pw → £1,625 pcm
        assert st.kv_get("rightmove_locid:ME4") == "OUTCODE^1652"
    finally:
        srv.shutdown()


@pytest.mark.skipif(not _browser_available(), reason="needs Playwright + a Chromium build")
def test_full_pipeline_with_portals_and_local_comps(monkeypatch, tmp_path):
    """Portals → local comps (no PropertyData) → underwriting, Land Registry stubbed."""
    if not os.environ.get("BRRR_CHROMIUM_PATH") and Path("/opt/pw-browsers/chromium").exists():
        monkeypatch.setenv("BRRR_CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    monkeypatch.delenv("PROPERTYDATA_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import src.local_comps as lc
    from src import config as config_mod
    from src.main import run_scan
    from src.storage import Storage
    from tests.portal_mock import portal_config, start

    monkeypatch.setattr(lc, "land_registry_sold", lambda oc, pt, ep: [245000, 250000, 255000, 260000])
    srv, base = start()
    try:
        cfg = config_mod.load_config()
        cfg["portals"] = portal_config(base)
        monkeypatch.setattr("src.main.load_config", lambda *_a, **_k: cfg)
        st = Storage(":memory:")
        s = run_scan(["ME4"], sources=["portals"], storage=st, dry_run=True)
        assert s.fetched == 4 and s.comps_source.startswith("portal")
        assert s.pre_screen_rejected == 2  # retirement flat + detached
        assert s.portals["onthemarket"]["state"] == "blocked"
        rows = st.query_deal_rows(include_ineligible=True)
        assert rows, s.as_dict()
        assert st.recent_runs()[0]["status"] == "partial"  # a portal was blocked
    finally:
        srv.shutdown()
