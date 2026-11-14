"""Pure, deterministic BRRR underwriting engine.

No I/O, no LLM calls. Every hard financial gate is evaluated here so the
pass/fail decision is reproducible and unit-testable. Claude only supplies
the refurb *tier*; the cost of that tier and everything downstream is Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import GateResult, PropertyCategory, RefurbTier, UnderwritingResult

# ---------------------------------------------------------------------------
# SDLT (England & NI, rates effective 1 April 2025)
# ---------------------------------------------------------------------------

# (upper bound of band, rate). Final band upper bound = inf.
RESIDENTIAL_BANDS: tuple[tuple[float, float], ...] = (
    (125_000, 0.00),
    (250_000, 0.02),
    (925_000, 0.05),
    (1_500_000, 0.10),
    (float("inf"), 0.12),
)

NON_RESIDENTIAL_BANDS: tuple[tuple[float, float], ...] = (
    (150_000, 0.00),
    (250_000, 0.02),
    (float("inf"), 0.05),
)

# Higher rates for additional dwellings do not apply below £40,000.
SURCHARGE_MIN_PRICE = 40_000


def _banded_tax(price: float, bands: tuple[tuple[float, float], ...]) -> float:
    tax = 0.0
    lower = 0.0
    for upper, rate in bands:
        if price <= lower:
            break
        taxable = min(price, upper) - lower
        tax += taxable * rate
        lower = upper
    return tax


def calculate_sdlt(
    price: float,
    category: PropertyCategory = PropertyCategory.RESIDENTIAL,
    surcharge_pct: float = 0.05,
) -> float:
    """SDLT for an SPV/additional-property purchase.

    Residential: standard bands + flat surcharge on the whole price (>= £40k).
    Commercial / mixed-use: non-residential bands, no surcharge.
    """
    if price <= 0:
        return 0.0
    if category in (PropertyCategory.COMMERCIAL, PropertyCategory.MIXED_USE):
        return round(_banded_tax(price, NON_RESIDENTIAL_BANDS), 2)
    tax = _banded_tax(price, RESIDENTIAL_BANDS)
    if price >= SURCHARGE_MIN_PRICE:
        tax += price * surcharge_pct
    return round(tax, 2)


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnderwritingParams:
    # thresholds
    all_in_cap_pct_of_arv: float = 0.75
    min_gross_yield: float = 0.068
    min_net_monthly_cashflow: float = 0.0
    min_capital_recycled_pct: float = 0.55
    icr_required: float = 1.25
    icr_stress_rate: float = 0.055
    # finance
    refi_ltv: float = 0.75
    btl_interest_rate: float = 0.0525
    operating_cost_drag_pct: float = 0.22
    corporation_tax_rate: float = 0.19
    icr_caps_loan: bool = False
    refi_fee_pct_of_loan: float = 0.02
    refi_fixed_costs: float = 1500.0
    use_bridging: bool = True
    bridging_ltv: float = 0.70
    bridging_monthly_rate: float = 0.0085
    bridging_arrangement_fee_pct: float = 0.02
    bridging_exit_fee_pct: float = 0.01
    bridging_months: int = 6
    # costs
    fixed_acquisition_costs: float = 3500.0
    sdlt_surcharge_pct: float = 0.05
    refurb_contingency_pct: float = 0.10
    refurb_per_sqft: dict[str, float] = field(
        default_factory=lambda: {"light": 18, "medium": 45, "heavy": 75, "structural": 120}
    )
    refurb_minimum: dict[str, float] = field(
        default_factory=lambda: {"light": 4000, "medium": 15000, "heavy": 30000, "structural": 50000}
    )

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> UnderwritingParams:
        t, f, c = cfg.get("thresholds", {}), cfg.get("finance", {}), cfg.get("costs", {})
        names = set(cls.__dataclass_fields__)
        kwargs: dict[str, Any] = {}
        for section in (t, f, c):
            for k, v in section.items():
                if k in names:
                    kwargs[k] = v
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Component calculators
# ---------------------------------------------------------------------------


def estimate_refurb_cost(tier: RefurbTier, floor_area_sqft: float, p: UnderwritingParams) -> float:
    """Tier rate × floor area, floored at the tier minimum, plus contingency."""
    base = max(p.refurb_per_sqft[tier] * floor_area_sqft, p.refurb_minimum[tier])
    return round(base * (1 + p.refurb_contingency_pct), 2)


def bridging_costs(purchase_price: float, p: UnderwritingParams) -> float:
    """Simple-interest retained bridge on the purchase: interest + arrangement + exit fees."""
    if not p.use_bridging:
        return 0.0
    loan = purchase_price * p.bridging_ltv
    interest = loan * p.bridging_monthly_rate * p.bridging_months
    fees = loan * (p.bridging_arrangement_fee_pct + p.bridging_exit_fee_pct)
    return round(interest + fees, 2)


def icr_max_loan(annual_rent: float, p: UnderwritingParams) -> float:
    """Largest loan satisfying rent >= ICR × stressed interest."""
    return annual_rent / (p.icr_required * p.icr_stress_rate)


def max_offer_price(
    arv: float,
    refurb_cost: float,
    p: UnderwritingParams,
    category: PropertyCategory = PropertyCategory.RESIDENTIAL,
) -> float:
    """Highest price that still clears Gate 1, via bisection (SDLT is monotonic)."""
    budget = p.all_in_cap_pct_of_arv * arv - refurb_cost - p.fixed_acquisition_costs
    if budget <= 0:
        return 0.0
    lo, hi = 0.0, budget
    for _ in range(80):
        mid = (lo + hi) / 2
        if mid + calculate_sdlt(mid, category, p.sdlt_surcharge_pct) <= budget:
            lo = mid
        else:
            hi = mid
    return float(int(lo // 500) * 500)  # round down to nearest £500 for offers


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def underwrite(
    *,
    listing_id: str,
    outcode: str,
    asking_price: float,
    conservative_arv: float,
    monthly_rent: float,
    floor_area_sqft: float,
    refurb_tier: RefurbTier,
    params: UnderwritingParams,
    category: PropertyCategory = PropertyCategory.RESIDENTIAL,
    refurb_cost_override: float | None = None,
) -> UnderwritingResult:
    p = params
    refurb = (
        round(refurb_cost_override, 2)
        if refurb_cost_override is not None
        else estimate_refurb_cost(refurb_tier, floor_area_sqft, p)
    )
    sdlt = calculate_sdlt(asking_price, category, p.sdlt_surcharge_pct)
    fixed = p.fixed_acquisition_costs
    bridge = bridging_costs(asking_price, p)

    # Gate 1 basis is exactly the spec formula.
    all_in = asking_price + refurb + sdlt + fixed
    cap = p.all_in_cap_pct_of_arv * conservative_arv

    # Refinance
    annual_rent = monthly_rent * 12
    refi_loan = conservative_arv * p.refi_ltv
    icr_cap = icr_max_loan(annual_rent, p)
    if p.icr_caps_loan:
        refi_loan = min(refi_loan, icr_cap)
    refi_costs = round(refi_loan * p.refi_fee_pct_of_loan + p.refi_fixed_costs, 2)
    net_refi_proceeds = refi_loan - refi_costs

    total_cash_deployed = all_in + bridge + refi_costs
    money_left_in = total_cash_deployed - refi_loan
    capital_recycled_pct = net_refi_proceeds / total_cash_deployed if total_cash_deployed else 0.0

    # SPV cashflow
    annual_interest = refi_loan * p.btl_interest_rate
    annual_opex = annual_rent * p.operating_cost_drag_pct
    pre_tax = annual_rent - annual_opex - annual_interest
    tax = pre_tax * p.corporation_tax_rate if pre_tax > 0 else 0.0
    net_annual = pre_tax - tax
    net_monthly = net_annual / 12

    stressed_interest = refi_loan * p.icr_stress_rate
    icr = annual_rent / stressed_interest if stressed_interest else float("inf")

    gross_yield = annual_rent / conservative_arv if conservative_arv else 0.0
    roce = None if money_left_in <= 0 else net_annual / money_left_in * 100

    gates = [
        GateResult(
            name="all_in_cap",
            passed=conservative_arv > 0 and round(all_in, 2) <= round(cap, 2),
            value=round(all_in / conservative_arv, 4) if conservative_arv else 0.0,
            threshold=p.all_in_cap_pct_of_arv,
            detail=f"All-in £{all_in:,.0f} vs cap £{cap:,.0f} ({p.all_in_cap_pct_of_arv:.0%} ARV)",
        ),
        GateResult(
            name="gross_yield",
            passed=gross_yield >= p.min_gross_yield,
            value=round(gross_yield, 4),
            threshold=p.min_gross_yield,
            detail=f"Rent £{annual_rent:,.0f}/yr on ARV £{conservative_arv:,.0f} = {gross_yield:.2%}",
        ),
        GateResult(
            name="net_cashflow",
            passed=net_monthly > p.min_net_monthly_cashflow,
            value=round(net_monthly, 2),
            threshold=p.min_net_monthly_cashflow,
            detail=f"Post-tax SPV cashflow £{net_monthly:,.0f}/mo",
        ),
        GateResult(
            name="capital_recycled",
            passed=capital_recycled_pct >= p.min_capital_recycled_pct,
            value=round(capital_recycled_pct, 4),
            threshold=p.min_capital_recycled_pct,
            detail=(
                f"Net refi £{net_refi_proceeds:,.0f} of £{total_cash_deployed:,.0f} deployed "
                f"= {capital_recycled_pct:.1%}"
            ),
        ),
    ]

    return UnderwritingResult(
        listing_id=listing_id,
        outcode=outcode,
        category=category,
        asking_price=round(asking_price, 2),
        conservative_arv=round(conservative_arv, 2),
        refurb_tier=refurb_tier,
        refurb_cost=refurb,
        sdlt=sdlt,
        fixed_acquisition_costs=fixed,
        bridging_costs=bridge,
        refi_costs=refi_costs,
        all_in_cost=round(all_in, 2),
        total_cash_deployed=round(total_cash_deployed, 2),
        refi_loan=round(refi_loan, 2),
        net_refi_proceeds=round(net_refi_proceeds, 2),
        money_left_in=round(money_left_in, 2),
        capital_recycled_pct=round(capital_recycled_pct, 4),
        annual_rent=round(annual_rent, 2),
        gross_yield=round(gross_yield, 4),
        annual_interest=round(annual_interest, 2),
        annual_opex=round(annual_opex, 2),
        pre_tax_profit=round(pre_tax, 2),
        corporation_tax=round(tax, 2),
        net_annual_cashflow=round(net_annual, 2),
        net_monthly_cashflow=round(net_monthly, 2),
        icr=round(icr, 3),
        icr_pass=icr >= p.icr_required,
        roce_pct=None if roce is None else round(roce, 2),
        max_offer_price=max_offer_price(conservative_arv, refurb, p, category),
        gates=gates,
        passed=all(g.passed for g in gates),
    )
