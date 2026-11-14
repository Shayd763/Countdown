"""Finance plans built from config, and re-underwriting saved deals under any plan.

Everything here is deterministic: comps and the refurb tier are taken from the saved
deal, so switching plan in the app never calls an API or Claude.
"""

from __future__ import annotations

from typing import Any

from .config import config_for_outcode
from .models import Deal, UnderwritingResult
from .underwriting import (
    BridgingProduct,
    FinancePlan,
    MortgageProduct,
    UnderwritingParams,
    underwrite,
)

MORTGAGE_FIELDS = set(MortgageProduct.__dataclass_fields__) - {"key"}
BRIDGING_FIELDS = set(BridgingProduct.__dataclass_fields__)


def mortgage_product(cfg: dict[str, Any], key: str) -> MortgageProduct:
    raw = (cfg.get("finance_products") or {}).get(key)
    if not raw:
        raise KeyError(f"unknown finance product {key!r}")
    return MortgageProduct(key=key, **{k: v for k, v in raw.items() if k in MORTGAGE_FIELDS})


def bridging_product(cfg: dict[str, Any]) -> BridgingProduct:
    raw = (cfg.get("finance_products") or {}).get("bridging") or {}
    return BridgingProduct(**{k: v for k, v in raw.items() if k in BRIDGING_FIELDS})


def plan_specs(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return list(cfg.get("finance_plans") or [])


def default_plan_id(cfg: dict[str, Any]) -> str:
    return (cfg.get("finance") or {}).get("default_plan") or "bridge_btl_ltd"


def build_plan(cfg: dict[str, Any], plan_id: str | None = None) -> FinancePlan:
    specs = {s["id"]: s for s in plan_specs(cfg)}
    plan_id = plan_id if plan_id in specs else default_plan_id(cfg)
    spec = specs.get(plan_id)
    fin = cfg.get("finance") or {}
    if spec is None:  # no plans configured: the original route
        from .underwriting import legacy_plan

        return legacy_plan(UnderwritingParams.from_config(cfg))
    purchase_key = spec["purchase"]
    refi_key = spec.get("refinance") or "none"
    refinance = None if refi_key in ("none", None) else mortgage_product(cfg, refi_key)
    if purchase_key in ("cash", "bridging"):
        purchase, purchase_mortgage = purchase_key, None
    else:
        purchase, purchase_mortgage = "mortgage", mortgage_product(cfg, purchase_key)
    owner_from = refinance or purchase_mortgage
    owner = owner_from.owner if owner_from else fin.get("cash_owner", "ltd")
    return FinancePlan(
        id=spec["id"], label=spec["label"], purchase=purchase, refinance=refinance,
        purchase_mortgage=purchase_mortgage,
        bridging=bridging_product(cfg) if purchase == "bridging" else None,
        months=int(fin.get("months_until_refinance", fin.get("bridging_months", 6))),
        owner=owner, main_residence=bool(spec.get("main_residence")),
        corporation_tax_rate=float(fin.get("corporation_tax_rate", 0.19)),
        personal_tax_rate=float(fin.get("personal_tax_rate", 0.40)),
        icr_caps_loan=bool(fin.get("icr_caps_loan", False)),
        note=spec.get("note", ""),
    )


def reunderwrite(deal: Deal, cfg: dict[str, Any], plan_id: str | None = None,
                 asking_price: float | None = None, refurb_tier: str | None = None,
                 refurb_cost: float | None = None, monthly_rent: float | None = None,
                 arv: float | None = None) -> UnderwritingResult:
    """The saved deal's numbers under a plan (and optional what-if overrides)."""
    lst, c = deal.listing, deal.comps
    ocfg = config_for_outcode(cfg, lst.outcode)
    res = underwrite(
        listing_id=lst.listing_id, outcode=lst.outcode,
        asking_price=asking_price or lst.asking_price,
        conservative_arv=arv or c.conservative_arv,
        monthly_rent=monthly_rent or c.conservative_monthly_rent,
        floor_area_sqft=c.floor_area_sqft, refurb_tier=refurb_tier or deal.refurb.tier,
        params=UnderwritingParams.from_config(ocfg), category=lst.category,
        refurb_cost_override=refurb_cost, plan=build_plan(ocfg, plan_id),
    )
    res.eligible = deal.result.eligible
    res.eligibility_reasons = list(deal.result.eligibility_reasons)
    return res


def compare_plans(deal: Deal, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for spec in plan_specs(cfg):
        r = reunderwrite(deal, cfg, spec["id"])
        out.append({
            "plan_id": spec["id"], "label": spec["label"], "short": spec.get("short", spec["label"]),
            "note": spec.get("note", ""), "ownership": r.ownership,
            "cash_to_buy": round(r.all_in_cost + r.bridging_costs, 2),
            "total_cash_deployed": r.total_cash_deployed, "long_term_loan": r.refi_loan,
            "money_left_in": r.money_left_in, "net_monthly_cashflow": r.net_monthly_cashflow,
            "roce_pct": r.roce_pct, "capital_recycled_pct": r.capital_recycled_pct,
            "passed": r.passed and r.eligible, "failed_gates": [g.name for g in r.gates if not g.passed],
        })
    return out


def finance_overview(cfg: dict[str, Any]) -> dict[str, Any]:
    fin = cfg.get("finance") or {}
    return {
        "products": cfg.get("finance_products") or {},
        "suggested": cfg.get("finance_suggested") or {},
        "plans": [{"id": s["id"], "label": s["label"], "short": s.get("short", s["label"]),
                   "note": s.get("note", "")} for s in plan_specs(cfg)],
        "default_plan": default_plan_id(cfg),
        "personal_tax_rate": fin.get("personal_tax_rate", 0.40),
        "corporation_tax_rate": fin.get("corporation_tax_rate", 0.19),
        "months_until_refinance": fin.get("months_until_refinance", 6),
    }
