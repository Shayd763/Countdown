"""Finance plans: exact maths for each route, and the plan-aware API."""

import time

import pytest
from fastapi.testclient import TestClient

from src.config import load_config
from src.finance import build_plan
from src.underwriting import BridgingProduct, FinancePlan, MortgageProduct, UnderwritingParams, underwrite

P = UnderwritingParams()
KW = dict(listing_id="t", outcode="ME4", asking_price=100_000, conservative_arv=200_000, monthly_rent=1_200,
          floor_area_sqft=900, refurb_tier="medium", params=P, refurb_cost_override=30_000)
BTL = MortgageProduct(key="btl_ltd", label="BTL", ltv=0.75, rate=0.0525, fee_pct=0.02, fixed_costs=1500)


def test_default_config_plan_matches_original_route():
    cfg = load_config()
    a = underwrite(**KW)
    b = underwrite(**KW, plan=build_plan(cfg))
    for f in ("sdlt", "bridging_costs", "refi_costs", "refi_loan", "money_left_in", "net_annual_cashflow",
              "corporation_tax", "capital_recycled_pct", "icr", "passed", "max_offer_price"):
        assert getattr(a, f) == getattr(b, f), f


def test_cash_purchase_has_no_finance_cost():
    plan = FinancePlan(id="c", label="c", purchase="cash", refinance=BTL)
    r = underwrite(**KW, plan=plan)
    # deployed = 138,500 + 0 + 4,500 refi costs = 143,000; left in = -7,000
    assert r.bridging_costs == 0 and r.total_cash_deployed == 143_000 and r.money_left_in == -7_000


def test_personal_ownership_uses_section_24():
    plan = FinancePlan(id="p", label="p", purchase="bridging", bridging=BridgingProduct(), refinance=BTL,
                       owner="personal", personal_tax_rate=0.40)
    r = underwrite(**KW, plan=plan)
    # rent 14,400 - opex 3,168 = 11,232 profit; tax 40% = 4,492.80 minus 20% of 7,875 interest = 1,575
    assert r.corporation_tax == pytest.approx(2_917.80, abs=0.01)
    assert r.net_annual_cashflow == pytest.approx(14_400 - 3_168 - 7_875 - 2_917.80, abs=0.01)
    assert r.ownership == "personal" and "Income tax" in r.tax_label


def test_repayment_mortgage_payment_and_tax_on_interest_only():
    com = MortgageProduct(key="c", label="Commercial", ltv=0.70, rate=0.06, repayment=True, term_years=20)
    plan = FinancePlan(id="x", label="x", purchase="cash", refinance=com)
    r = underwrite(**KW, plan=plan)
    loan = 140_000
    m = 0.06 / 12
    pmt = loan * m / (1 - (1 + m) ** -240) * 12  # 12,036.23 a year
    assert r.refi_loan == loan
    assert r.annual_mortgage_payment == pytest.approx(pmt, abs=0.01)
    assert r.annual_interest == pytest.approx(8_400, abs=0.01)
    assert r.annual_capital_repaid == pytest.approx(pmt - 8_400, abs=0.01)
    # tax on rent - opex - interest only (capital isn't deductible): (14,400-3,168-8,400) x 19%
    assert r.corporation_tax == pytest.approx((14_400 - 3_168 - 8_400) * 0.19, abs=0.01)


def test_main_residence_pays_no_surcharge():
    res = MortgageProduct(key="r", label="Res", ltv=0.75, rate=0.044, repayment=True, owner="personal",
                          kind="residential", rate_loading=0.01)
    plan = FinancePlan(id="r", label="r", purchase="mortgage", purchase_mortgage=res, refinance=res,
                       owner="personal", main_residence=True)
    r = underwrite(**{**KW, "asking_price": 200_000}, plan=plan)
    assert r.sdlt == 1_500.0  # 75k x 2%, no 5% surcharge
    assert r.annual_interest == pytest.approx(150_000 * 0.054, abs=0.01)  # rate + consent-to-let loading


def test_mortgage_from_day_one_costs_interest_during_works():
    plan = FinancePlan(id="d", label="d", purchase="mortgage", purchase_mortgage=BTL, refinance=BTL, months=6)
    r = underwrite(**KW, plan=plan)
    # 75k loan: 6 months interest 1,968.75 + 2% fee 1,500 + 1,500 fixed = 4,968.75
    assert r.purchase_loan == 75_000 and r.bridging_costs == pytest.approx(4_968.75, abs=0.01)


def test_all_cash_no_mortgage_skips_capital_recycled():
    """No refinance means no capital comes back by design: gate 4 doesn't apply to that plan."""
    plan = FinancePlan(id="a", label="a", purchase="cash", refinance=None)
    r = underwrite(**KW, plan=plan)
    assert r.refi_loan == 0 and r.money_left_in == 138_500 and r.annual_mortgage_payment == 0
    g4 = next(g for g in r.gates if g.name == "capital_recycled")
    assert g4.passed and "Not applicable" in g4.detail and r.capital_recycled_pct == 0
    assert r.passed == all(g.passed for g in r.gates if g.name != "capital_recycled")
    assert r.net_annual_cashflow == pytest.approx((14_400 - 3_168) * 0.81, abs=0.01)


def test_refinance_plans_still_need_capital_recycled():
    plan = FinancePlan(id="b", label="b", purchase="cash", refinance=None)
    with_refi = underwrite(**KW)  # default plan refinances
    assert with_refi.capital_recycled_pct > 0
    g4 = next(g for g in with_refi.gates if g.name == "capital_recycled")
    assert g4.passed == (with_refi.capital_recycled_pct >= 0.55)
    assert "Not applicable" not in g4.detail and plan.refinance is None


def test_every_configured_plan_builds():
    cfg = load_config()
    ids = [s["id"] for s in cfg["finance_plans"]]
    assert "bridge_btl_ltd" in ids and "residential" in ids and len(ids) >= 6
    for pid in ids:
        r = underwrite(**KW, plan=build_plan(cfg, pid))
        assert r.plan_id == pid


@pytest.fixture()
def client(tmp_path, monkeypatch):
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
    return c


def test_plan_switch_changes_numbers_everywhere(client):
    c = client
    base = {d["listing_id"]: d for d in c.get("/api/deals", params={"include_ineligible": True}).json()}
    cash = {d["listing_id"]: d for d in c.get("/api/deals", params={"include_ineligible": True,
                                                                    "plan": "cash_only"}).json()}
    lid = next(iter(base))
    assert base[lid]["plan"] == "Bridge, then Ltd buy-to-let" and cash[lid]["plan"] == "All cash, no mortgage"
    assert cash[lid]["refi_loan"] == 0
    # all-cash deals are judged on the first three gates only (no refinance, so gate 4 doesn't apply)
    full = c.get(f"/api/deals/{lid}", params={"plan": "cash_only"}).json()["result"]
    g = {x["name"]: x["passed"] for x in full["gates"]}
    assert g["capital_recycled"] and full["passed"] == (g["all_in_cap"] and g["gross_yield"] and g["net_cashflow"])
    # the person's saved plan becomes their default view and digest
    c.put("/api/auth/me", json={"prefs": {"plan": "cash_only"}})
    assert c.get("/api/digest").json()["plan"]["id"] == "cash_only"
    assert c.get(f"/api/deals/{lid}").json()["result"]["plan_id"] == "cash_only"
    plans = c.get(f"/api/deals/{lid}/plans").json()
    assert {p["plan_id"] for p in plans} >= {"bridge_btl_ltd", "cash_only", "residential"}
    w = c.post(f"/api/deals/{lid}/whatif", json={"plan": "bridge_btl_ltd"}).json()
    assert w["plan_id"] == "bridge_btl_ltd"


def test_finance_settings_validate_and_apply(client):
    c = client
    f = c.get("/api/finance").json()
    assert f["can_edit"] and f["suggested"]["products"]["btl_ltd"]["rate"] > 0
    assert c.put("/api/finance", json={"products": {"btl_ltd": {"rate": 2}}}).status_code == 400
    assert c.put("/api/finance", json={"products": {"nope": {"rate": 0.05}}}).status_code == 400
    lid = c.get("/api/deals").json()[0]["listing_id"]
    before = c.get(f"/api/deals/{lid}").json()["result"]["annual_interest"]
    r = c.put("/api/finance", json={"products": {"btl_ltd": {"rate": 0.0625}}, "personal_tax_rate": 0.2})
    assert r.status_code == 200 and r.json()["products"]["btl_ltd"]["rate"] == 0.0625
    after = c.get(f"/api/deals/{lid}").json()["result"]["annual_interest"]
    assert after == pytest.approx(before / 0.0525 * 0.0625, rel=1e-6)


def test_config_reads_utf8_regardless_of_locale(monkeypatch):
    """Windows' default codepage turned 'Bridge → Ltd BTL' into 'Bridge â†’ Ltd BTL'."""
    import locale

    from src.config import load_config

    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252")
    shorts = [p["short"] for p in load_config()["finance_plans"]]
    assert "Bridge → Ltd BTL" in shorts
