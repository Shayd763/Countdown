"""Per-user daily digest: what passed, what nearly passed, and the money for each.

Used by the phone app's Today screen (`/api/digest`) and by the push notification sent
at the end of each scheduled run.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import load_config
from .models import Deal
from .storage import Storage

log = logging.getLogger(__name__)


def money(d: Deal) -> dict[str, Any]:
    """The numbers that matter at a glance, all from the deterministic engine."""
    r = d.result
    return {
        "asking_price": r.asking_price,
        "max_offer_price": r.max_offer_price,
        "sdlt": r.sdlt,
        "refurb_cost": r.refurb_cost,
        "fees": round(r.fixed_acquisition_costs + r.bridging_costs + r.refi_costs, 2),
        "cash_to_buy": r.all_in_cost + r.bridging_costs,  # price+refurb+SDLT+fees+bridge
        "total_cash_deployed": r.total_cash_deployed,
        "arv": r.conservative_arv,
        "refi_loan": r.refi_loan,
        "net_refi_proceeds": r.net_refi_proceeds,
        "money_left_in": r.money_left_in,
        "monthly_rent": round(r.annual_rent / 12, 2),
        "net_monthly_cashflow": r.net_monthly_cashflow,
        "net_annual_cashflow": r.net_annual_cashflow,
        "gross_yield": r.gross_yield,
        "roce_pct": r.roce_pct,
        "capital_recycled_pct": r.capital_recycled_pct,
    }


def card(d: Deal, meta: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    lst, r = d.listing, d.result
    meta = meta or {}
    return {
        "listing_id": lst.listing_id, "address": lst.address or lst.postcode, "postcode": lst.postcode,
        "outcode": lst.outcode, "region": lst.region, "property_type": lst.property_type,
        "bedrooms": lst.bedrooms,
        "category": lst.category.value, "source": lst.source, "url": lst.url,
        "image": (lst.image_urls or [None])[0], "is_auction": lst.is_auction,
        "refurb_tier": r.refurb_tier, "red_flags": len(d.refurb.red_flags),
        "passed": r.passed and r.eligible, "eligible": r.eligible,
        "gates": {g.name: g.passed for g in r.gates}, "status": meta.get("status", "new"),
        "first_seen": meta.get("first_seen"), "price_drop_pct": d.price_drop_pct,
        "reduced_on": lst.reduced_on, "listed_on": lst.listed_on, "search_kind": lst.search_kind,
        **money(d), **extra,
    }


def _matches(d: Deal, prefs: dict[str, Any]) -> bool:
    from .geo import matches_place

    lst, r = d.listing, d.result
    if not matches_place(lst.outcode, lst.postcode, regions=prefs.get("regions") or None,
                         prefixes=prefs.get("outcodes") or None):
        return False
    if prefs.get("property_types") and lst.property_type not in prefs["property_types"]:
        return False
    if prefs.get("categories") and lst.category.value not in prefs["categories"]:
        return False
    auctions = prefs.get("auctions") or "any"
    if (auctions == "only" and not lst.is_auction) or (auctions == "hide" and lst.is_auction):
        return False
    if prefs.get("min_roce") not in (None, "") and r.roce_pct is not None \
            and r.roce_pct < float(prefs["min_roce"]):
        return False
    return not (prefs.get("max_cash_in") not in (None, "")
                and r.total_cash_deployed > float(prefs["max_cash_in"]))


def build_digest(st: Storage, prefs: dict[str, Any]) -> dict[str, Any]:
    from .finance import build_plan, reunderwrite

    runs = [r for r in st.recent_runs(10) if r["status"] != "running"]
    last = runs[0] if runs else None
    new_since = last["started_at"] if (last and prefs.get("only_new")) else None
    cfg = load_config()
    plan_id = prefs.get("plan") or None
    rows = st.query_deal_rows(hide_rejected=True, limit=2000, sort="roce_pct")
    for r in rows:  # every number under the person's chosen finance plan and current settings
        r["deal"] = r["deal"].model_copy(update={"result": reunderwrite(r["deal"], cfg, plan_id)})
    loose = [r for r in rows if _matches(r["deal"], {**prefs, "auctions": "any"})]
    rows = [r for r in loose if _matches(r["deal"], prefs)]
    rows.sort(key=lambda r: (r["deal"].result.roce_pct is None, r["deal"].result.roce_pct or 0), reverse=True)
    if new_since:
        rows = [r for r in rows if (r["first_seen"] or "") >= new_since]
    # Passing deals the auction / "new that morning" settings are hiding, so the app can say so.
    ok = lambda r: r["deal"].result.passed and r["deal"].result.eligible  # noqa: E731
    shown = {id(r) for r in rows}
    hidden_pass = [r for r in loose if ok(r) and id(r) not in shown]
    hidden = {"passing": len(hidden_pass),
              "auctions": sum(1 for r in hidden_pass if not _matches(r["deal"], prefs)),
              "only_new": sum(1 for r in hidden_pass if _matches(r["deal"], prefs))}

    passing = [r for r in rows if r["deal"].result.passed and r["deal"].result.eligible]
    # Near misses: eligible deals that would pass all four gates at the max offer price.
    near: list[dict[str, Any]] = []
    for r in rows:
        d = r["deal"]
        if d.result.passed or not d.result.eligible or d.result.max_offer_price <= 0:
            continue
        gap = 1 - d.result.max_offer_price / d.result.asking_price
        if gap > 0.25:
            continue
        alt = reunderwrite(d, cfg, plan_id, asking_price=d.result.max_offer_price)
        if alt.passed:
            near.append(card(d, r, offer_gap_pct=round(gap * 100, 1),
                             at_offer_cashflow=alt.net_monthly_cashflow,
                             at_offer_money_left_in=alt.money_left_in, at_offer_roce=alt.roce_pct))
    near.sort(key=lambda x: x["offer_gap_pct"])

    size = int(prefs.get("digest_size") or 5)
    best = [card(r["deal"], r) for r in passing[:size]]
    summary = last.get("summary") if last else None
    plan = build_plan(cfg, plan_id)
    return {
        "plan": {"id": plan.id, "label": plan.label},
        "funnel": ((summary or {}).get("funnel") or {}).get("total"),
        "run": {"started_at": last["started_at"], "finished_at": last.get("finished_at"),
                "status": last["status"], "sources": last.get("sources"),
                "fetched": (summary or {}).get("fetched"),
                "new_passing": (summary or {}).get("new_passing")} if last else None,
        "counts": {"matching": len(rows), "passing": len(passing), "near_misses": len(near)},
        "hidden": hidden,
        "totals": {
            "cash_needed_all_passing": round(sum(r["deal"].result.total_cash_deployed for r in passing), 2),
            "monthly_cashflow_all_passing": round(sum(r["deal"].result.net_monthly_cashflow
                                                      for r in passing), 2),
        },
        "best": best,
        "near_misses": near[:size],
    }


def _gbp(x: float | None) -> str:
    return "–" if x is None else ("−£" if x < 0 else "£") + f"{abs(x):,.0f}"


def notification_for(digest: dict[str, Any], name: str = "") -> dict[str, Any] | None:
    n, near = digest["counts"]["passing"], digest["counts"]["near_misses"]
    if n:
        top = digest["best"][0]
        roce = "∞" if top["roce_pct"] is None else f"{top['roce_pct']:.0f}%"
        title = f"{n} BRRR deal{'s' if n != 1 else ''} pass today"
        body = (f"Best: {top['address']} — {_gbp(top['asking_price'])}, cash in "
                f"{_gbp(top['total_cash_deployed'])}, refi {_gbp(top['refi_loan'])}, "
                f"{_gbp(top['net_monthly_cashflow'])}/mo, ROCE {roce}")
    elif near:
        top = digest["near_misses"][0]
        title = "No passes today — " + f"{near} negotiable"
        body = (f"{top['address']} works at {_gbp(top['max_offer_price'])} "
                f"({top['offer_gap_pct']:.0f}% under asking)")
    else:
        title = "BRRR digest: nothing passes today"
        body = f"{digest['counts']['matching']} listings checked."
    return {"title": title, "body": body[:230], "url": "/app/#/today", "tag": "brrr-digest",
            "digest": {"counts": digest["counts"], "best": [
                {k: c[k] for k in ("listing_id", "address", "asking_price", "total_cash_deployed",
                                   "refi_loan", "net_monthly_cashflow", "roce_pct")}
                for c in digest["best"][:3]]}}


def send_daily_digests(st: Storage) -> dict[str, Any]:
    """Called at the end of a scheduled run: one push per subscribed user, per their preferences."""
    from .auth import Auth
    from .push import send_to_user, user_subscriptions

    results: dict[str, Any] = {}
    for user in Auth(st).list_users():
        prefs = user["prefs"]
        if prefs.get("notify") == "off" or not user_subscriptions(st, user["id"]):
            continue
        dg = build_digest(st, prefs)
        if prefs.get("notify") == "passes" and not dg["counts"]["passing"]:
            continue
        msg = notification_for(dg, user["name"])
        try:
            results[user["email"]] = send_to_user(st, user["id"], msg)
        except Exception as exc:  # noqa: BLE001 — never fail the run over a notification
            log.warning("digest push failed for user %s: %s", user["id"], exc)
            results[user["email"]] = {"error": str(exc)}
    st.audit("digests_sent", None, {"users": len(results)})
    return results
