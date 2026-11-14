"""Comparable evidence: sold prices, £/sq ft, rents, EPC and floor areas.

Primary source is PropertyData. HM Land Registry Price Paid (SPARQL) is the
fallback for sold comps and the EPC register is the fallback for floor area.
Output is a `CompData` with a *conservative* ARV and rent — deterministic
arithmetic only.
"""

from __future__ import annotations

import base64
import logging
import os
import statistics
from datetime import date, timedelta
from typing import Any

import httpx

from .http import DataClient, PropertyDataError, get_with_retry
from .ingestion import fetch_asking_prices
from .models import CompData, PropertyCategory, RawListing

log = logging.getLogger(__name__)

SQM_TO_SQFT = 10.7639

PD_TYPE = {
    "flat": "flat",
    "maisonette": "flat",
    "terraced": "terraced_house",
    "end-terrace": "terraced_house",
    "semi-detached": "semi-detached_house",
    "detached": "detached_house",
    "bungalow": "detached_house",
}

LR_TYPE = {  # Land Registry property type URIs (suffix)
    "flat": "flat-maisonette",
    "maisonette": "flat-maisonette",
    "terraced": "terraced",
    "end-terrace": "terraced",
    "semi-detached": "semi-detached",
    "detached": "detached",
    "bungalow": "detached",
}


def _num(x: Any) -> float | None:
    try:
        return float(str(x).replace(",", "").replace("£", "")) if x not in (None, "") else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Individual sources
# ---------------------------------------------------------------------------


def pd_sold_prices(client: DataClient, outcode: str, ptype: str, beds: int | None) -> list[float]:
    try:
        data = client.get(
            "sold-prices", postcode=outcode, type=PD_TYPE.get(ptype), max_age=24
        ).get("data", {})
    except PropertyDataError as exc:
        log.info("sold-prices %s failed: %s", outcode, exc)
        return []
    raw = data.get("raw_data") or []
    prices = []
    for r in raw:
        if beds is not None and r.get("bedrooms") not in (None, beds):
            continue
        v = _num(r.get("price"))
        if v:
            prices.append(v)
    return prices


def pd_price_per_sqft(client: DataClient, outcode: str) -> float | None:
    try:
        data = client.get("sold-prices-per-sqf", postcode=outcode).get("data", {})
    except PropertyDataError:
        return None
    return _num(data.get("average"))


def pd_rent(client: DataClient, outcode: str, beds: int | None) -> tuple[float | None, float | None]:
    try:
        data = client.get("rents", postcode=outcode, bedrooms=beds).get("data", {})
    except PropertyDataError:
        return None, None
    ll = data.get("long_let") or data
    avg = _num(ll.get("average"))
    rng = ll.get("70pc_range") or []
    low = _num(rng[0]) if rng else None
    # PropertyData reports rents weekly; convert to monthly if flagged.
    if (ll.get("units") or "").startswith("gbp_per_week"):
        avg = avg * 52 / 12 if avg else None
        low = low * 52 / 12 if low else None
    return avg, low


def pd_floor_area(client: DataClient, listing: RawListing) -> float | None:
    try:
        data = client.get("floor-areas", postcode=listing.postcode or listing.outcode).get("data", {})
    except PropertyDataError:
        return None
    known = data.get("known_floor_areas") or []
    addr = listing.address.lower()
    for k in known:
        if k.get("address") and k["address"].lower().split(",")[0] in addr:
            return _num(k.get("square_feet"))
    return None


def pd_epc(client: DataClient, listing: RawListing) -> str | None:
    try:
        data = client.get("energy-efficiency", postcode=listing.postcode or listing.outcode).get(
            "data", {}
        )
    except PropertyDataError:
        return None
    rows = data.get("energy_efficiency") or []
    addr = listing.address.lower()
    for r in rows:
        if r.get("address") and r["address"].lower().split(",")[0] in addr:
            return r.get("rating")
    return None


def land_registry_sold(outcode: str, ptype: str, endpoint: str, months: int = 24) -> list[float]:
    """HM Land Registry Price Paid via SPARQL — free fallback for sold comps."""
    since = (date.today() - timedelta(days=30 * months)).isoformat()
    lr_type = LR_TYPE.get(ptype)
    type_clause = (
        f"?tx lrppi:propertyType <http://landregistry.data.gov.uk/def/common/{lr_type}> ."
        if lr_type else ""
    )
    query = f"""
PREFIX lrppi: <http://landregistry.data.gov.uk/def/ppi/>
PREFIX lrcommon: <http://landregistry.data.gov.uk/def/common/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?amount WHERE {{
  ?tx lrppi:pricePaid ?amount ;
      lrppi:transactionDate ?date ;
      lrppi:propertyAddress ?addr .
  ?addr lrcommon:postcode ?pc .
  {type_clause}
  FILTER(STRSTARTS(?pc, "{outcode.upper()} "))
  FILTER(?date >= "{since}"^^xsd:date)
}} LIMIT 400"""
    try:
        with httpx.Client(timeout=45) as http:
            resp = get_with_retry(
                http, endpoint, params={"query": query, "output": "json"}, attempts=2,
                headers={"Accept": "application/sparql-results+json"},
            )
            rows = resp.json()["results"]["bindings"]
    except Exception as exc:  # noqa: BLE001
        log.info("Land Registry SPARQL failed for %s: %s", outcode, exc)
        return []
    return [float(r["amount"]["value"]) for r in rows if "amount" in r]


def epc_floor_area(listing: RawListing, base_url: str) -> tuple[float | None, str | None]:
    """EPC register lookup by postcode: (floor area sqft, current rating)."""
    token = os.environ.get("EPC_API_TOKEN")
    email = os.environ.get("EPC_API_EMAIL")
    if not token or not listing.postcode:
        return None, None
    auth = base64.b64encode(f"{email}:{token}".encode()).decode() if email else token
    try:
        with httpx.Client(timeout=20) as http:
            resp = get_with_retry(
                http, f"{base_url.rstrip('/')}/domestic/search",
                params={"postcode": listing.postcode, "size": 100}, attempts=2,
                headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
            )
            rows = resp.json().get("rows", [])
    except Exception as exc:  # noqa: BLE001
        log.info("EPC lookup failed for %s: %s", listing.postcode, exc)
        return None, None
    first_line = listing.address.lower().split(",")[0]
    for r in rows:
        if first_line and first_line in (r.get("address") or "").lower():
            area = _num(r.get("total-floor-area"))
            return (area * SQM_TO_SQFT if area else None), r.get("current-energy-rating")
    return None, None


# ---------------------------------------------------------------------------
# Floor area resolution + conservative valuation
# ---------------------------------------------------------------------------


def default_floor_area(ptype: str, beds: int | None, cfg: dict[str, Any]) -> float:
    table = cfg["default_floor_area_sqft"]
    key = "commercial" if ptype in ("commercial", "mixed-use") else ptype
    key = {"end-terrace": "terraced", "maisonette": "flat", "bungalow": "detached"}.get(key, key)
    by_beds = table.get(key) or table["terraced"]
    by_beds = {int(k): v for k, v in by_beds.items()}
    if beds in by_beds:
        return float(by_beds[beds])
    nearest = min(by_beds, key=lambda b: abs(b - (beds or 2)))
    return float(by_beds[nearest])


def conservative_value(estimates: dict[str, float], haircut: float) -> float:
    """Median of available estimates, discounted by the haircut."""
    vals = [v for v in estimates.values() if v and v > 0]
    if not vals:
        return 0.0
    return round(statistics.median(vals) * (1 - haircut), 2)


def enrich(listing: RawListing, client: DataClient, cfg: dict[str, Any], *,
           use_external_fallbacks: bool = True) -> CompData:
    costs = cfg["costs"]
    ing = cfg["ingestion"]
    sources: list[str] = []
    oc, ptype, beds = listing.outcode, listing.property_type, listing.bedrooms

    # Floor area: listing -> PropertyData -> EPC -> default table
    sqft, sqft_src = listing.floor_area_sqft, "listing"
    epc_rating = None
    if not sqft:
        sqft, sqft_src = pd_floor_area(client, listing), "propertydata"
    if not sqft and use_external_fallbacks:
        sqft, epc_rating = epc_floor_area(listing, ing["epc_base_url"])
        sqft_src = "epc"
    if not sqft:
        sqft, sqft_src = default_floor_area(ptype, beds, cfg), "default"
    sources.append(f"floor_area:{sqft_src}")

    if listing.category != PropertyCategory.RESIDENTIAL:
        com = cfg["commercial"]
        rate = com["rent_per_sqft_pa"].get(oc, com["rent_per_sqft_pa"]["default"])
        annual_rent = rate * sqft
        arv_est = {"income_capitalisation": annual_rent / com["valuation_yield"]}
        monthly = annual_rent / 12
        return CompData(
            outcode=oc, property_type=ptype, bedrooms=beds, floor_area_sqft=sqft,
            floor_area_source=sqft_src, monthly_rent_average=round(monthly, 2),
            arv_estimates={k: round(v, 2) for k, v in arv_est.items()},
            conservative_arv=conservative_value(arv_est, costs["arv_haircut_pct"]),
            conservative_monthly_rent=round(monthly * (1 - costs["rent_haircut_pct"]), 2),
            sources=[*sources, "commercial:config_rent_yield"],
        )

    epc_rating = epc_rating or pd_epc(client, listing)

    sold = pd_sold_prices(client, oc, ptype, beds)
    if sold:
        sources.append("sold:propertydata")
    elif use_external_fallbacks:
        sold = land_registry_sold(oc, ptype, ing["land_registry_sparql"])
        if sold:
            sources.append("sold:land_registry")
    ppsf = pd_price_per_sqft(client, oc)
    asking = fetch_asking_prices(client, oc, beds, PD_TYPE.get(ptype))
    asking_avg = _num(asking.get("average"))
    rent_avg, rent_low = pd_rent(client, oc, beds)

    estimates: dict[str, float] = {}
    if sold:
        estimates["sold_median"] = statistics.median(sold)
    if ppsf:
        estimates["sold_ppsf_x_area"] = ppsf * sqft
        sources.append("ppsf:propertydata")
    if asking_avg:
        estimates["asking_avg_less_5pct"] = asking_avg * 0.95
        sources.append("asking:propertydata")

    rent_basis = rent_avg or 0.0
    if rent_avg:
        sources.append("rent:propertydata")

    return CompData(
        outcode=oc,
        property_type=ptype,
        bedrooms=beds,
        floor_area_sqft=round(sqft, 1),
        floor_area_source=sqft_src,
        sold_price_median=round(statistics.median(sold), 2) if sold else None,
        sold_comp_count=len(sold),
        sold_price_per_sqft=ppsf,
        asking_price_average=asking_avg,
        monthly_rent_average=rent_avg,
        monthly_rent_low=rent_low,
        epc_rating=epc_rating,
        arv_estimates={k: round(v, 2) for k, v in estimates.items()},
        conservative_arv=conservative_value(estimates, costs["arv_haircut_pct"]),
        conservative_monthly_rent=round(rent_basis * (1 - costs["rent_haircut_pct"]), 2),
        sources=sources,
    )
