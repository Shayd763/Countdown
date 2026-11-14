"""Exact-figure tests for the deterministic underwriting engine.

Every expected number below is worked by hand in the comment beside it.
"""

import pytest

from src.models import PropertyCategory
from src.underwriting import (
    UnderwritingParams,
    bridging_costs,
    calculate_sdlt,
    estimate_refurb_cost,
    icr_max_loan,
    max_offer_price,
    underwrite,
)

P = UnderwritingParams()  # defaults mirror config/postcodes.yaml


# --------------------------------------------------------------------------- SDLT


@pytest.mark.parametrize(
    "price, expected",
    [
        (39_999, 0.0),  # below £40k: no surcharge, below nil-rate band
        (40_000, 2_000.0),  # 5% surcharge kicks in at £40k: 40,000 × 5%
        (125_000, 6_250.0),  # 0 + 125k × 5%
        (150_000, 8_000.0),  # 25k × 2% = 500; + 150k × 5% = 7,500
        (250_000, 15_000.0),  # 125k × 2% = 2,500; + 12,500
        (300_000, 20_000.0),  # 2,500 + 50k × 5% = 2,500; + 15,000
        (1_000_000, 93_750.0),  # 2,500 + 33,750 + 7,500 = 43,750; + 50,000 surcharge
    ],
)
def test_sdlt_residential_with_surcharge(price, expected):
    assert calculate_sdlt(price) == expected


def test_sdlt_residential_without_surcharge():
    assert calculate_sdlt(300_000, surcharge_pct=0.0) == 5_000.0


@pytest.mark.parametrize(
    "price, expected",
    [
        (150_000, 0.0),
        (200_000, 1_000.0),  # 50k × 2%
        (300_000, 4_500.0),  # 100k × 2% = 2,000 + 50k × 5% = 2,500
    ],
)
def test_sdlt_non_residential(price, expected):
    assert calculate_sdlt(price, PropertyCategory.COMMERCIAL) == expected
    assert calculate_sdlt(price, PropertyCategory.MIXED_USE) == expected


def test_sdlt_zero_and_negative():
    assert calculate_sdlt(0) == 0.0
    assert calculate_sdlt(-5) == 0.0


# --------------------------------------------------------------------------- components


def test_bridging_costs():
    # loan = 100k × 70% = 70,000; interest = 70,000 × 0.85% × 6 = 3,570
    # fees = 70,000 × (2% + 1%) = 2,100 → 5,670
    assert bridging_costs(100_000, P) == 5_670.0


def test_bridging_disabled():
    assert bridging_costs(100_000, UnderwritingParams(use_bridging=False)) == 0.0


@pytest.mark.parametrize(
    "tier, sqft, expected",
    [
        ("medium", 900, 44_550.0),  # 900 × 45 = 40,500 × 1.10
        ("light", 100, 4_400.0),  # 100 × 18 = 1,800 < 4,000 min → 4,000 × 1.10
        ("heavy", 1000, 82_500.0),  # 75,000 × 1.10
        ("structural", 1000, 132_000.0),  # 120,000 × 1.10
    ],
)
def test_refurb_tiers(tier, sqft, expected):
    assert estimate_refurb_cost(tier, sqft, P) == pytest.approx(expected)


def test_icr_max_loan():
    # 14,400 / (1.25 × 5.5%) = 14,400 / 0.06875 = 209,454.545...
    assert icr_max_loan(14_400, P) == pytest.approx(209_454.5454, rel=1e-6)


# --------------------------------------------------------------------------- full deal

# Reference deal: £100k asking, ARV £200k, rent £1,200/mo, refurb override £30k
REF = dict(
    listing_id="t1", outcode="ME4", asking_price=100_000, conservative_arv=200_000,
    monthly_rent=1_200, floor_area_sqft=900, refurb_tier="medium", params=P,
    refurb_cost_override=30_000,
)


def test_reference_deal_exact_figures():
    r = underwrite(**REF)
    # SDLT on 100k: 0 bands + 5% surcharge = 5,000
    assert r.sdlt == 5_000.0
    # Gate 1 basis: 100,000 + 30,000 + 5,000 + 3,500 = 138,500 ; cap 150,000
    assert r.all_in_cost == 138_500.0
    # Refi: 75% × 200k = 150,000 ; costs = 2% × 150k + 1,500 = 4,500
    assert r.refi_loan == 150_000.0
    assert r.refi_costs == 4_500.0
    assert r.net_refi_proceeds == 145_500.0
    # Bridging 5,670 (see test_bridging_costs)
    assert r.bridging_costs == 5_670.0
    # Deployed = 138,500 + 5,670 + 4,500 = 148,670 ; left in = 148,670 − 150,000 = −1,330
    assert r.total_cash_deployed == 148_670.0
    assert r.money_left_in == -1_330.0
    # Recycled = 145,500 / 148,670 = 0.97868...
    assert r.capital_recycled_pct == pytest.approx(0.9787, abs=1e-4)
    # Cashflow: rent 14,400 ; opex 22% = 3,168 ; interest 150k × 5.25% = 7,875
    assert r.annual_rent == 14_400.0
    assert r.annual_opex == 3_168.0
    assert r.annual_interest == 7_875.0
    # Pre-tax 3,357 ; CT 19% = 637.83 ; net 2,719.17 ; monthly 226.60
    assert r.pre_tax_profit == 3_357.0
    assert r.corporation_tax == 637.83
    assert r.net_annual_cashflow == 2_719.17
    assert r.net_monthly_cashflow == pytest.approx(226.60, abs=0.01)
    # ICR = 14,400 / (150,000 × 5.5% = 8,250) = 1.745
    assert r.icr == pytest.approx(1.745, abs=1e-3)
    assert r.icr_pass
    # Yield 14,400 / 200,000 = 7.2%
    assert r.gross_yield == 0.072
    # No money left in → infinite ROCE
    assert r.roce_pct is None
    assert r.roce_display == "∞"
    assert r.passed
    assert all(g.passed for g in r.gates)


def test_gate1_fails_when_all_in_exceeds_cap():
    # Asking 115k: SDLT 5,750 → all-in 115,000 + 30,000 + 5,750 + 3,500 = 154,250 > 150,000
    r = underwrite(**{**REF, "asking_price": 115_000})
    gate = next(g for g in r.gates if g.name == "all_in_cap")
    assert not gate.passed
    assert r.all_in_cost == 154_250.0
    assert not r.passed


def test_gate1_boundary_is_inclusive():
    # Choose asking so all-in == cap exactly: p + 0.05p = 150,000 − 30,000 − 3,500 = 116,500
    p = 116_500 / 1.05
    r = underwrite(**{**REF, "asking_price": p})
    assert next(g for g in r.gates if g.name == "all_in_cap").passed


def test_gate2_yield_threshold():
    # 6.8% of 200k = 13,600/yr = 1,133.33/mo. 1,130/mo → 6.78% fails.
    r = underwrite(**{**REF, "monthly_rent": 1_130})
    assert not next(g for g in r.gates if g.name == "gross_yield").passed
    r2 = underwrite(**{**REF, "monthly_rent": 1_134})
    assert next(g for g in r2.gates if g.name == "gross_yield").passed


def test_gate3_negative_cashflow_no_tax_credit():
    # Rent 900/mo: 10,800 − 2,376 − 7,875 = 549 → still positive (444.69 post-tax)
    r = underwrite(**{**REF, "monthly_rent": 900})
    assert r.net_annual_cashflow == pytest.approx(549 * 0.81, abs=0.01)
    # Rent 800/mo: 9,600 − 2,112 − 7,875 = −387 → loss, no CT applied
    r2 = underwrite(**{**REF, "monthly_rent": 800})
    assert r2.pre_tax_profit == -387.0
    assert r2.corporation_tax == 0.0
    assert not next(g for g in r2.gates if g.name == "net_cashflow").passed


def test_gate4_capital_recycled_and_roce():
    # Asking 60k on ARV 120k, refurb 40k: SDLT 3,000; all-in 106,500 > cap 90,000 (gate1 fail)
    # Refi 90,000; refi costs 3,300; bridge = 42,000 × (0.051 + 0.03) = 3,402
    # Deployed 106,500 + 3,402 + 3,300 = 113,202 ; recycled = 86,700 / 113,202 = 76.6%
    r = underwrite(**{**REF, "asking_price": 60_000, "conservative_arv": 120_000,
                      "refurb_cost_override": 40_000, "monthly_rent": 800})
    assert r.total_cash_deployed == 113_202.0
    assert r.capital_recycled_pct == pytest.approx(0.7659, abs=1e-4)
    assert next(g for g in r.gates if g.name == "capital_recycled").passed
    # Money left in 23,202. Cashflow: 9,600 − 2,112 − 4,725 = 2,763 × 0.81 = 2,238.03
    assert r.money_left_in == 23_202.0
    assert r.roce_pct == pytest.approx(2_238.03 / 23_202 * 100, abs=0.01)


def test_gate4_fails_on_overpaying():
    r = underwrite(**{**REF, "asking_price": 250_000, "refurb_cost_override": 60_000})
    g = next(g for g in r.gates if g.name == "capital_recycled")
    assert not g.passed and g.value < 0.55


def test_icr_caps_loan_when_enabled():
    # Rent 900/mo → ICR cap 10,800 / 0.06875 = 157,090.9 > 150,000 so LTV binds
    p = UnderwritingParams(icr_caps_loan=True)
    r = underwrite(**{**REF, "params": p, "monthly_rent": 900})
    assert r.refi_loan == 150_000.0
    # Rent 700/mo → cap 8,400 / 0.06875 = 122,181.82 binds
    r2 = underwrite(**{**REF, "params": p, "monthly_rent": 700})
    assert r2.refi_loan == pytest.approx(122_181.82, abs=0.01)
    assert r2.icr == pytest.approx(1.25, abs=1e-3)


def test_commercial_uses_non_residential_sdlt():
    r = underwrite(**{**REF, "category": PropertyCategory.COMMERCIAL, "asking_price": 200_000,
                      "conservative_arv": 400_000})
    assert r.sdlt == 1_000.0


def test_max_offer_price_clears_gate1():
    mo = max_offer_price(200_000, 30_000, P)
    # budget = 150,000 − 30,000 − 3,500 = 116,500 ; p × 1.05 ≤ 116,500 → 110,952 → £110,500
    assert mo == 110_500.0
    r = underwrite(**{**REF, "asking_price": mo})
    assert next(g for g in r.gates if g.name == "all_in_cap").passed
    assert r.max_offer_price == mo


def test_max_offer_zero_when_refurb_exceeds_budget():
    assert max_offer_price(50_000, 60_000, P) == 0.0


def test_params_from_config_roundtrip():
    from src.config import load_config
    cfg = load_config()
    p = UnderwritingParams.from_config(cfg)
    assert p.min_gross_yield == 0.068
    assert p.all_in_cap_pct_of_arv == 0.75
    assert p.btl_interest_rate == 0.0525
    assert p.operating_cost_drag_pct == 0.22
    assert p.corporation_tax_rate == 0.19
    assert p.min_capital_recycled_pct == 0.55
    assert p.refurb_per_sqft["medium"] == 45
