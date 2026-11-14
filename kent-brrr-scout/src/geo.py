"""UK postcode geography: postcode parsing, postcode area -> region, coverage, and
reverse geocoding (lat/lng -> outcode) through postcodes.io, cached in the database.

Coverage is England for now: Wales (Land Transaction Tax) and Scotland (LBTT and the
Additional Dwelling Supplement) tax purchases differently, so the gates would be wrong there.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I)
OUTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\b")

REGIONS = ["North East", "North West", "Yorkshire and the Humber", "East Midlands", "West Midlands",
           "East of England", "London", "South East", "South West"]

# Postcode area -> English region. Areas that straddle a boundary go to the region holding
# most of their addresses (for example DA to the South East, EN and KT to London).
AREA_REGION: dict[str, str] = {
    **dict.fromkeys(["E", "EC", "N", "NW", "SE", "SW", "W", "WC", "BR", "CR", "EN", "HA", "IG", "KT", "RM",
                     "SM", "TW", "UB"], "London"),
    **dict.fromkeys(["BN", "CT", "DA", "GU", "HP", "ME", "MK", "OX", "PO", "RG", "RH", "SL", "SO", "TN"],
                    "South East"),
    **dict.fromkeys(["AL", "CB", "CM", "CO", "IP", "LU", "NR", "PE", "SG", "SS", "WD"], "East of England"),
    **dict.fromkeys(["BA", "BH", "BS", "DT", "EX", "GL", "PL", "SN", "SP", "TA", "TQ", "TR"], "South West"),
    **dict.fromkeys(["B", "CV", "DY", "HR", "ST", "SY", "TF", "WR", "WS", "WV"], "West Midlands"),
    **dict.fromkeys(["DE", "LE", "LN", "NG", "NN"], "East Midlands"),
    **dict.fromkeys(["BD", "DN", "HD", "HG", "HU", "HX", "LS", "S", "WF", "YO"], "Yorkshire and the Humber"),
    **dict.fromkeys(["BB", "BL", "CA", "CH", "CW", "FY", "L", "LA", "M", "OL", "PR", "SK", "WA", "WN"],
                    "North West"),
    **dict.fromkeys(["DH", "DL", "NE", "SR", "TS"], "North East"),
}
OUTSIDE_ENGLAND = {
    **dict.fromkeys(["CF", "LD", "LL", "NP", "SA"], "Wales"),
    **dict.fromkeys(["AB", "DD", "DG", "EH", "FK", "G", "HS", "IV", "KA", "KW", "KY", "ML", "PA", "PH", "TD",
                     "ZE"], "Scotland"),
    "BT": "Northern Ireland", "GY": "Channel Islands", "JE": "Channel Islands", "IM": "Isle of Man",
}


ENGLAND_COUNTIES = [
    "Bedfordshire", "Berkshire", "Bristol", "Buckinghamshire", "Cambridgeshire", "Cheshire", "Cornwall", "Cumbria",
    "Derbyshire", "Devon", "Dorset", "County Durham", "East Riding of Yorkshire", "East Sussex", "Essex",
    "Gloucestershire", "London", "Greater Manchester", "Hampshire", "Herefordshire", "Hertfordshire",
    "Isle of Wight", "Kent", "Lancashire", "Leicestershire", "Lincolnshire", "Merseyside", "Norfolk",
    "North Yorkshire", "Northamptonshire", "Northumberland", "Nottinghamshire", "Oxfordshire", "Rutland",
    "Shropshire", "Somerset", "South Yorkshire", "Staffordshire", "Suffolk", "Surrey", "Tyne and Wear",
    "Warwickshire", "West Midlands", "West Sussex", "West Yorkshire", "Wiltshire", "Worcestershire",
]


def area_of(outcode: str) -> str:
    m = re.match(r"[A-Z]{1,2}", (outcode or "").upper())
    return m.group(0) if m else ""


def region_of(outcode: str) -> str:
    a = area_of(outcode)
    return AREA_REGION.get(a) or OUTSIDE_ENGLAND.get(a, "")


def in_england(outcode: str) -> bool:
    return area_of(outcode) in AREA_REGION


def parse_postcode(text: str) -> tuple[str, str]:
    """(full postcode or '', outcode or '') found in free text."""
    m = POSTCODE_RE.search(text or "")
    if m and area_of(m.group(1)) in AREA_REGION | OUTSIDE_ENGLAND:
        return f"{m.group(1).upper()} {m.group(2).upper()}", m.group(1).upper()
    # an outcode alone, e.g. "High Street, Chatham ME4"; only trust it at the end of the text
    m2 = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*$", (text or "").strip().upper())
    if m2 and area_of(m2.group(1)) in AREA_REGION | OUTSIDE_ENGLAND:
        return "", m2.group(1)
    return "", ""


def in_coverage(outcode: str, cfg: dict[str, Any]) -> bool:
    scope = (cfg.get("coverage") or {}).get("scope", "england")
    if scope == "focus":
        from .config import all_outcodes

        return outcode.upper() in set(all_outcodes(cfg))
    return in_england(outcode)


def matches_place(outcode: str, postcode: str, *, regions: list[str] | None = None,
                  prefixes: list[str] | None = None) -> bool:
    """True if a listing is in one of the regions and starts with one of the postcode prefixes
    (an area like 'M', a district like 'ME4', or a sector like 'ME4 6'). Empty lists match all."""
    if regions and region_of(outcode) not in regions:
        return False
    if prefixes:
        pc = (postcode or outcode or "").upper().replace(" ", "")
        oc = (outcode or "").upper()
        for p in prefixes:
            p = p.upper().replace(" ", "")
            if not p:
                continue
            if p.isalpha():  # an area: must match the letters exactly (M != ME)
                if area_of(oc) == p:
                    return True
            elif oc == p or pc.startswith(p):
                return True
        return False
    return True


# ---------------------------------------------------------------------------
# Reverse geocoding through postcodes.io (free, no key), cached in kv
# ---------------------------------------------------------------------------


def reverse_geocode(points: list[tuple[float, float]], storage, base_url: str = "https://api.postcodes.io",
                    timeout: float = 20) -> dict[tuple[float, float], str]:
    """{(lat, lng): outcode} for the points postcodes.io can place; cached per ~100 m."""
    out: dict[tuple[float, float], str] = {}
    todo: list[tuple[float, float]] = []
    for lat, lng in points:
        key = f"geo:{lat:.3f},{lng:.3f}"
        cached = storage.kv_get(key)
        if cached is not None:
            if cached:
                out[(lat, lng)] = cached
        else:
            todo.append((lat, lng))
    for i in range(0, len(todo), 100):
        batch = todo[i:i + 100]
        body = {"geolocations": [{"latitude": la, "longitude": ln, "limit": 1, "radius": 500} for la, ln in batch]}
        try:
            r = httpx.post(f"{base_url.rstrip('/')}/postcodes", json=body, timeout=timeout)
            r.raise_for_status()
            results = r.json().get("result") or []
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("postcodes.io reverse geocode failed: %s", exc)
            break
        for (la, ln), res in zip(batch, results, strict=False):
            hits = (res or {}).get("result") or []
            oc = hits[0].get("outcode", "") if hits else ""
            storage.kv_set(f"geo:{la:.3f},{ln:.3f}", oc)
            if oc:
                out[(la, ln)] = oc
    return out
