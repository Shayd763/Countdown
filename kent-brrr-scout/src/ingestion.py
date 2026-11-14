"""Listing ingestion: PropertyData sourced lists + auction RSS feeds, normalised to RawListing.

Also holds the deterministic eligibility filter, which runs *before* any paid
enrichment calls so excluded stock never costs API credits.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from .http import DataClient, PropertyDataError, get_with_retry
from .models import PropertyCategory, RawListing, Tenure

log = logging.getLogger(__name__)

POSTCODE_RE = re.compile(r"\b((?:ME|CT)\d{1,2})\s?(\d[A-Z]{2})\b", re.I)
PRICE_RE = re.compile(r"£\s?([\d,]{4,})")
LEASE_RE = re.compile(r"(\d{2,3})\s*(?:years?|yrs?)\s*(?:remaining|left|unexpired|lease)", re.I)
LEASE_RE_2 = re.compile(r"lease(?:hold)?[^.]{0,40}?(\d{2,3})\s*(?:years?|yrs?)", re.I)
BEDS_RE = re.compile(r"\b(\d|one|two|three|four|five|six)\s*(?:-|\s)?bed", re.I)
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_TYPE_MAP = [
    ("end of terrace", "end-terrace"),
    ("end-terrace", "end-terrace"),
    ("end_terrace", "end-terrace"),
    ("semi", "semi-detached"),
    ("terrace", "terraced"),
    ("maisonette", "maisonette"),
    ("flat", "flat"),
    ("apartment", "flat"),
    ("bungalow", "bungalow"),
    ("detached", "detached"),
    ("mixed", "mixed-use"),
    ("shop", "commercial"),
    ("retail", "commercial"),
    ("office", "commercial"),
    ("commercial", "commercial"),
    ("industrial", "commercial"),
    ("warehouse", "commercial"),
    ("land", "land"),
    ("house", "terraced"),  # generic "house" — treat as terraced, flagged by Claude if wrong
]


def normalise_type(raw: str | None) -> str:
    s = (raw or "").lower().replace("_", " ")
    for needle, mapped in _TYPE_MAP:
        if needle.replace("_", " ") in s:
            return mapped
    return "unknown"


def category_for_type(ptype: str, text: str = "") -> PropertyCategory:
    t = text.lower()
    if ptype == "mixed-use" or "flat above" in t or "with residential above" in t:
        return PropertyCategory.MIXED_USE
    if ptype == "commercial":
        return PropertyCategory.COMMERCIAL
    return PropertyCategory.RESIDENTIAL


def infer_tenure(text: str) -> tuple[Tenure, int | None]:
    t = text.lower()
    lease_years = None
    for rx in (LEASE_RE, LEASE_RE_2):
        m = rx.search(text)
        if m:
            lease_years = int(m.group(1))
            break
    if "share of freehold" in t:
        return Tenure.SHARE_OF_FREEHOLD, lease_years
    if "leasehold" in t or lease_years is not None:
        return Tenure.LEASEHOLD, lease_years
    if "freehold" in t:
        return Tenure.FREEHOLD, None
    return Tenure.UNKNOWN, None


def outcode_of(postcode: str) -> str:
    return postcode.strip().upper().split(" ")[0] if postcode else ""


def _stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# PropertyData sourced properties
# ---------------------------------------------------------------------------


def parse_sourced_property(item: dict[str, Any], list_name: str) -> RawListing | None:
    try:
        price = float(item.get("price") or item.get("asking_price") or 0)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    postcode = str(item.get("postcode") or "").upper()
    desc = str(item.get("description") or item.get("summary") or "")
    ptype = normalise_type(item.get("type") or item.get("property_type"))
    tenure_raw = str(item.get("tenure") or "")
    tenure, lease_years = infer_tenure(f"{tenure_raw} {desc}")
    if item.get("lease_years_remaining"):
        lease_years = int(item["lease_years_remaining"])
    beds = item.get("bedrooms")
    images = item.get("images") or item.get("image_urls") or []
    if isinstance(images, str):
        images = [images]
    return RawListing(
        listing_id=f"pd-{item.get('id') or _stable_id(postcode, str(price), str(item.get('url')))}",
        source=f"propertydata:{list_name}",
        url=item.get("url"),
        address=str(item.get("address") or item.get("title") or ""),
        postcode=postcode,
        outcode=outcode_of(postcode),
        asking_price=price,
        property_type=ptype,
        category=category_for_type(ptype, desc),
        tenure=tenure,
        lease_years_remaining=lease_years,
        bedrooms=int(beds) if beds not in (None, "") else None,
        floor_area_sqft=float(item["sqf"]) if item.get("sqf") else None,
        description=desc,
        image_urls=list(images),
        floorplan_urls=list(item.get("floorplans") or []),
        days_on_market=item.get("days_on_market"),
        is_auction="auction" in list_name,
        lat=item.get("lat"),
        lng=item.get("lng"),
    )


def fetch_sourced(client: DataClient, outcode: str, cfg: dict[str, Any]) -> list[RawListing]:
    ing = cfg["ingestion"]
    out: dict[str, RawListing] = {}
    for list_name in ing["sourced_lists"]:
        try:
            data = client.get(
                "sourced-properties", list=list_name, postcode=outcode, max_price=ing.get("max_price")
            )
        except PropertyDataError as exc:
            log.warning("sourced-properties %s/%s failed: %s", outcode, list_name, exc)
            continue
        for item in data.get("properties", []):
            listing = parse_sourced_property(item, list_name)
            if listing and listing.outcode == outcode.upper():
                # First list wins, but keep the auction flag if any list says so.
                if listing.listing_id in out:
                    out[listing.listing_id].is_auction |= listing.is_auction
                else:
                    out[listing.listing_id] = listing
    return list(out.values())


def fetch_asking_prices(
    client: DataClient, outcode: str, bedrooms: int | None, pd_type: str | None = None
) -> dict[str, Any]:
    """PropertyData /prices — current asking-price stats for the outcode."""
    try:
        return client.get("prices", postcode=outcode, bedrooms=bedrooms, type=pd_type).get("data", {})
    except PropertyDataError as exc:
        log.info("prices %s failed: %s", outcode, exc)
        return {}


# ---------------------------------------------------------------------------
# RSS / Atom auction feeds (Clive Emson etc.)
# ---------------------------------------------------------------------------


def parse_rss(xml_text: str, feed_name: str, target_outcodes: set[str]) -> list[RawListing]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("bad RSS from %s: %s", feed_name, exc)
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    listings: list[RawListing] = []
    for it in items:
        def txt(tag: str, _it: ET.Element = it) -> str:
            el = _it.find(tag)
            if el is None:
                el = _it.find(f"atom:{tag}", ns)
            if el is None:
                return ""
            return (el.text or el.get("href") or "").strip()

        title, desc = txt("title"), re.sub(r"<[^>]+>", " ", txt("description") or txt("summary"))
        link = txt("link")
        blob = f"{title} {desc}"
        pc = POSTCODE_RE.search(blob)
        price_m = PRICE_RE.search(blob)
        if not pc or not price_m:
            continue
        outcode = pc.group(1).upper()
        if outcode not in target_outcodes:
            continue
        ptype = normalise_type(blob)
        tenure, lease = infer_tenure(blob)
        beds = BEDS_RE.search(blob)
        images = [
            enc.get("url") for enc in it.findall("enclosure")
            if enc.get("url") and (enc.get("type") or "").startswith("image")
        ]
        listings.append(
            RawListing(
                listing_id=f"rss-{_stable_id(feed_name, link or title)}",
                source=f"rss:{feed_name}",
                url=link or None,
                address=title,
                postcode=f"{outcode} {pc.group(2).upper()}",
                outcode=outcode,
                asking_price=float(price_m.group(1).replace(",", "")),
                property_type=ptype,
                category=category_for_type(ptype, blob),
                tenure=tenure,
                lease_years_remaining=lease,
                bedrooms=(_WORD_NUM.get(beds.group(1).lower()) or int(beds.group(1)))
                if beds else None,
                description=desc.strip(),
                image_urls=images,
                is_auction=True,
            )
        )
    return listings


def fetch_rss(cfg: dict[str, Any], target_outcodes: set[str]) -> list[RawListing]:
    out: list[RawListing] = []
    with httpx.Client(timeout=20, follow_redirects=True,
                      headers={"User-Agent": "kent-brrr-scout/0.1"}) as http:
        for url in cfg["ingestion"].get("rss_feeds", []):
            try:
                resp = get_with_retry(http, url, attempts=2)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001 — feed outages must not kill the run
                log.warning("RSS %s failed: %s", url, exc)
                continue
            name = httpx.URL(url).host.replace("www.", "").split(".")[0]
            out.extend(parse_rss(resp.text, name, target_outcodes))
    return out


# ---------------------------------------------------------------------------
# Deterministic eligibility filter
# ---------------------------------------------------------------------------


def check_eligibility(
    listing: RawListing, cfg: dict[str, Any], *, allow_unknown_lease: bool = False
) -> tuple[bool, list[str]]:
    """Hard property filters. ``allow_unknown_lease`` is used for the cheap pre-screen,
    before Claude has had a chance to read the lease length out of the description."""
    rules = cfg["eligibility"]
    reasons: list[str] = []
    text = f"{listing.address} {listing.description}".lower()

    for kw in rules["exclude_keywords"]:
        if kw.lower() in text:
            reasons.append(f"excluded keyword: '{kw}'")

    if listing.category != PropertyCategory.RESIDENTIAL:
        if not rules.get("include_commercial", False):
            reasons.append(f"{listing.category.value} excluded by config")
        return (not reasons, reasons)

    ptype = listing.property_type
    if ptype not in rules["allowed_residential_types"]:
        reasons.append(f"property type '{ptype}' not in target types")
    if ptype in ("flat", "maisonette"):
        if listing.tenure == Tenure.FREEHOLD:
            pass  # freehold flat — unusual but fine
        elif listing.lease_years_remaining is None:
            if not allow_unknown_lease:
                reasons.append("flat with unknown lease length")
        elif listing.lease_years_remaining <= rules["min_lease_years_flats"]:
            reasons.append(
                f"lease {listing.lease_years_remaining}y <= {rules['min_lease_years_flats']}y"
            )
    elif listing.tenure == Tenure.LEASEHOLD:
        reasons.append("leasehold house")
    return (not reasons, reasons)
