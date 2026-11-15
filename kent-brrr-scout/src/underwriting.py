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
    surcharge_pct: float | None = None,
) -> float:
    """Highest price that still clears Gate 1, via bisection (SDLT is monotonic)."""
    surcharge = p.sdlt_surcharge_pct if surcharge_pct is None else surcharge_pct
    budget = p.all_in_cap_pct_of_arv * arv - refurb_cost - p.fixed_acquisition_costs
    if budget <= 0:
        return 0.0
    lo, hi = 0.0, budget
    for _ in range(80):
        mid = (lo + hi) / 2
        if mid + calculate_sdlt(mid, category, surcharge) <= budget:
            lo = mid
        else:
            hi = mid
    return float(int(lo // 500) * 500)  # round down to nearest £500 for offers


# ---------------------------------------------------------------------------
# Finance products and plans
# ---------------------------------------------------------------------------

SECTION_24_CREDIT = 0.20  # basic-rate tax credit on finance costs for individual landlords


@dataclass(frozen=True)
class MortgageProduct:
    key: str
    label: str
    ltv: float
    rate: float
    fee_pct: float = 0.0
    fixed_costs: float = 0.0
    repayment: bool = False
    term_years: int = 25
    icr: float = 1.25
    stress_rate: float = 0.055
    erc_pct: float = 0.0          # early repayment charge if refinanced inside a fixed period
    rate_loading: float = 0.0     # e.g. consent-to-let on a residential mortgage
    owner: str = "ltd"            # "ltd" | "personal"
    kind: str = "btl"             # "btl" | "commercial" | "residential"

    @property
    def pay_rate(self) -> float:
        return self.rate + self.rate_loading

    def annual_payment(self, loan: float) -> tuple[float, float]:
        """(first-year interest, first-year total payment)."""
        if loan <= 0:
            return 0.0, 0.0
        interest = loan * self.pay_rate
        if not self.repayment:
            return interest, interest
        r, n = self.pay_rate / 12, self.term_years * 12
        pmt = loan / n if r == 0 else loan * r / (1 - (1 + r) ** -n)
        return interest, pmt * 12


@dataclass(frozen=True)
class BridgingProduct:
    ltv: float = 0.70
    monthly_rate: float = 0.0085
    arrangement_fee_pct: float = 0.02
    exit_fee_pct: float = 0.01
    fixed_costs: float = 0.0
    funds_refurb_pct: float = 0.0


@dataclass(frozen=True)
class FinancePlan:
    id: str
    label: str
    purchase: str                          # "cash" | "bridging" | "mortgage"
    refinance: MortgageProduct | None      # long-term loan taken after the works
    purchase_mortgage: MortgageProduct | None = None
    bridging: BridgingProduct | None = None
    months: int = 6                        # purchase to refinance
    owner: str = "ltd"
    main_residence: bool = False           # no stamp duty surcharge
    corporation_tax_rate: float = 0.19
    personal_tax_rate: float = 0.40
    icr_caps_loan: bool = False
    note: str = ""


def legacy_plan(p: UnderwritingParams) -> FinancePlan:
    """The original single route: bridge the purchase, refinance onto a Ltd buy-to-let."""
    btl = MortgageProduct(key="btl_ltd", label="Buy-to-let mortgage (Ltd)", ltv=p.refi_ltv,
                          rate=p.btl_interest_rate, fee_pct=p.refi_fee_pct_of_loan,
                          fixed_costs=p.refi_fixed_costs, icr=p.icr_required, stress_rate=p.icr_stress_rate)
    bridge = BridgingProduct(ltv=p.bridging_ltv, monthly_rate=p.bridging_monthly_rate,
                             arrangement_fee_pct=p.bridging_arrangement_fee_pct,
                             exit_fee_pct=p.bridging_exit_fee_pct)
    return FinancePlan(id="bridge_btl_ltd", label="Bridge, then Ltd buy-to-let",
                       purchase="bridging" if p.use_bridging else "cash", refinance=btl,
                       bridging=bridge if p.use_bridging else None, months=p.bridging_months,
                       owner="ltd", corporation_tax_rate=p.corporation_tax_rate,
                       icr_caps_loan=p.icr_caps_loan)


def purchase_finance(plan: FinancePlan, price: float, refurb: float) -> tuple[float, float]:
    """(loan used to buy, finance cost from purchase to refinance)."""
    if plan.purchase == "bridging" and plan.bridging:
        b = plan.bridging
        loan = price * b.ltv + refurb * b.funds_refurb_pct
        interest = loan * b.monthly_rate * plan.months
        cost = interest + loan * (b.arrangement_fee_pct + b.exit_fee_pct) + b.fixed_costs
        return loan, round(cost, 2)
    if plan.purchase == "mortgage" and plan.purchase_mortgage:
        m = plan.purchase_mortgage
        loan = price * m.ltv
        interest, payment = m.annual_payment(loan)
        cost = payment * plan.months / 12 + loan * m.fee_pct + m.fixed_costs
        if plan.refinance:
            cost += loan * m.erc_pct
        return loan, round(cost, 2)
    return 0.0, 0.0


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
    plan: FinancePlan | None = None,
) -> UnderwritingResult:
    p = params
    plan = plan or legacy_plan(p)
    refurb = (
        round(refurb_cost_override, 2)
        if refurb_cost_override is not None
        else estimate_refurb_cost(refurb_tier, floor_area_sqft, p)
    )
    surcharge = 0.0 if plan.main_residence else p.sdlt_surcharge_pct
    sdlt = calculate_sdlt(asking_price, category, surcharge)
    fixed = p.fixed_acquisition_costs

    # Gate 1 basis is exactly the spec formula.
    all_in = asking_price + refurb + sdlt + fixed
    cap = p.all_in_cap_pct_of_arv * conservative_arv

    # Buying and holding until the refinance
    purchase_loan, purchase_costs = purchase_finance(plan, asking_price, refurb)

    # The long-term loan: a refinance on the new value, or the purchase mortgage kept
    annual_rent = monthly_rent * 12
    product = plan.refinance or (plan.purchase_mortgage if plan.purchase == "mortgage" else None)
    if plan.refinance:
        r = plan.refinance
        long_loan = conservative_arv * r.ltv
        if plan.icr_caps_loan and r.kind == "btl":
            long_loan = min(long_loan, annual_rent / (r.icr * r.stress_rate))
        refi_costs = round(long_loan * r.fee_pct + r.fixed_costs, 2)
    else:
        long_loan = purchase_loan if product else 0.0
        refi_costs = 0.0
    net_refi_proceeds = long_loan - refi_costs

    total_cash_deployed = all_in + purchase_costs + refi_costs
    money_left_in = total_cash_deployed - long_loan
    capital_recycled_pct = net_refi_proceeds / total_cash_deployed if total_cash_deployed else 0.0

    # Holding it, once let
    annual_interest, annual_payment = product.annual_payment(long_loan) if product else (0.0, 0.0)
    annual_opex = annual_rent * p.operating_cost_drag_pct
    pre_tax = annual_rent - annual_opex - annual_interest
    if plan.owner == "ltd":
        tax = pre_tax * plan.corporation_tax_rate if pre_tax > 0 else 0.0
        tax_label = "Corporation tax"
    else:  # individual landlord: Section 24, finance costs get a 20% credit, not a deduction
        property_profit = max(0.0, annual_rent - annual_opex)
        tax = max(0.0, property_profit * plan.personal_tax_rate
                  - SECTION_24_CREDIT * min(annual_interest, property_profit))
        tax_label = f"Income tax at {plan.personal_tax_rate:.0%}, after the 20% interest credit"
    net_annual = annual_rent - annual_opex - annual_payment - tax
    net_monthly = net_annual / 12

    stress_rate = 0.0 if product is None else (product.stress_rate if product.kind == "btl" else product.pay_rate)
    stressed_interest = long_loan * stress_rate
    icr = annual_rent / stressed_interest if stressed_interest else float("inf")
    icr_needed = product.icr if product and product.kind == "btl" else 1.0

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
            detail=f"Post-tax cashflow £{net_monthly:,.0f}/mo ({plan.label})",
        ),
        GateResult(
            name="capital_recycled",
            # A plan with no refinance (all cash, held) never recycles capital by design, so this
            # gate doesn't apply to it; judge those deals on the other three gates.
            passed=plan.refinance is None or capital_recycled_pct >= p.min_capital_recycled_pct,
            value=round(capital_recycled_pct, 4),
            threshold=p.min_capital_recycled_pct,
            detail=(
                f"Not applicable: no refinance in this plan ({plan.label}); cash stays in the deal"
                if plan.refinance is None else
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
        bridging_costs=purchase_costs,
        refi_costs=refi_costs,
        all_in_cost=round(all_in, 2),
        total_cash_deployed=round(total_cash_deployed, 2),
        refi_loan=round(long_loan, 2),
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
        icr_pass=icr >= icr_needed,
        roce_pct=None if roce is None else round(roce, 2),
        max_offer_price=max_offer_price(conservative_arv, refurb, p, category, surcharge),
        gates=gates,
        passed=all(g.passed for g in gates),
        plan_id=plan.id,
        plan_label=plan.label,
        ownership=plan.owner,
        purchase_method=plan.purchase if plan.purchase != "mortgage" else plan.purchase_mortgage.key,
        purchase_loan=round(purchase_loan, 2),
        long_term_product=product.label if product else "No mortgage",
        annual_mortgage_payment=round(annual_payment, 2),
        annual_capital_repaid=round(annual_payment - annual_interest, 2),
        tax_label=tax_label,
    )
