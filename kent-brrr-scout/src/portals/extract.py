"""Turn portal pages into listing dicts.

Portals change their markup often, so extraction is layered and generic rather
than tied to one page structure:

1. Embedded JSON (``__NEXT_DATA__``, ``window.jsonModel``, ``window.PAGE_MODEL``,
   ``application/json`` scripts, Next.js flight data): walk the tree and keep any
   dict that looks like a listing (an id plus a price plus an address/title).
2. JSON-LD (``application/ld+json``) residence / offer items.
3. DOM cards: the visible text around each link to a detail page, read with regexes.

Everything here is pure (HTML/text in, dicts out) so it is unit-tested offline.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from typing import Any
from urllib.parse import urljoin

from ..ingestion import normalise_type

PRICE_RE = re.compile(r"£\s?([\d,]{3,})(?:\.\d{2})?\s*(pcm|pw|per\s+week|per\s+month|p/?w|p/?cm)?", re.I)
BEDS_RE = re.compile(r"\b(\d{1,2})\s*(?:-|\s)?bed(?:room)?s?\b", re.I)
SQFT_RE = re.compile(r"([\d,]{3,5})\s*(?:sq\.?\s?ft|sqft|square\s+feet)", re.I)
SQM_RE = re.compile(r"([\d,.]{2,6})\s*(?:sq\.?\s?m\b|sqm|m²|square\s+met)", re.I)
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*(\d[A-Z]{2})?\b")
LEASE_RE = re.compile(r"(\d{2,3})\s*(?:years?|yrs?)\s*(?:remaining|left|unexpired|on the lease)", re.I)

BLOCK_MARKERS = [
    "access denied", "are you a robot", "unusual traffic", "verify you are human",
    "verify you are a human", "just a moment", "attention required", "captcha",
    "request blocked", "pardon our interruption", "please enable cookies and javascript",
]


def detect_block(status: int | None, title: str, text: str) -> str | None:
    """Return a reason if the page is a block / challenge page rather than content."""
    if status in (403, 429, 503):
        return f"HTTP {status}"
    hay = f"{title}\n{text[:3000]}".lower()
    for m in BLOCK_MARKERS:
        if m in hay:
            return f"page says '{m}'"
    return None


# ---------------------------------------------------------------------------
# JSON blobs
# ---------------------------------------------------------------------------

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
_ASSIGN_RE = re.compile(r"window\.(jsonModel|PAGE_MODEL|__PRELOADED_STATE__|__INITIAL_STATE__)\s*=\s*", re.S)
_FLIGHT_RE = re.compile(r"self\.__next_f\.push\(\[\d+,\s*(\".*?\")\]\)", re.S)

_decoder = json.JSONDecoder()


def _decode_at(text: str, start: int) -> Any | None:
    try:
        obj, _ = _decoder.raw_decode(text, start)
        return obj
    except (json.JSONDecodeError, ValueError):
        return None


def json_blobs(page_html: str) -> list[Any]:
    """All JSON-ish payloads embedded in a page."""
    blobs: list[Any] = []
    flight_parts: list[str] = []
    for attrs, body in _SCRIPT_RE.findall(page_html):
        a = attrs.lower()
        body = body.strip()
        if not body:
            continue
        if "application/ld+json" in a or "application/json" in a:
            obj = _decode_at(body, 0)
            if obj is not None:
                blobs.append(obj)
            continue
        for m in _ASSIGN_RE.finditer(body):
            obj = _decode_at(body, m.end())
            if obj is not None:
                blobs.append(obj)
        for m in _FLIGHT_RE.finditer(body):
            try:
                flight_parts.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                pass
    if flight_parts:
        flight = "".join(flight_parts)
        for key in ('{"listingId"', '{"listing_id"', '{"propertyId"'):
            for m in re.finditer(re.escape(key), flight):
                obj = _decode_at(flight, m.start())
                if obj is not None:
                    blobs.append(obj)
    return blobs


_ID_KEYS = ("listingId", "listing_id", "propertyId", "property_id", "id")
_PRICE_KEYS = ("price", "pricing", "priceValue", "price_value", "displayPrice", "amount")
_ADDR_KEYS = ("displayAddress", "address", "displayable_address", "addressLine", "title", "streetAddress")


def _looks_like_listing(d: dict) -> bool:
    return (any(k in d for k in _ID_KEYS) and any(k in d for k in _PRICE_KEYS)
            and any(k in d for k in _ADDR_KEYS))


def find_listing_dicts(obj: Any, _depth: int = 0) -> list[dict]:
    out: list[dict] = []
    if _depth > 40:
        return out
    if isinstance(obj, dict):
        if _looks_like_listing(obj):
            out.append(obj)
            return out  # don't descend into a listing's own sub-objects
        for v in obj.values():
            out += find_listing_dicts(v, _depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            out += find_listing_dicts(v, _depth + 1)
    return out


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def parse_money(v: Any) -> tuple[float | None, str | None]:
    """Price from int / '£1,234 pcm' / {'amount': 1234, 'frequency': 'weekly'} shapes.
    Returns (amount, frequency) where frequency is 'weekly', 'monthly' or None."""
    if v is None:
        return None, None
    if isinstance(v, int | float):
        return float(v), None
    if isinstance(v, dict):
        freq = str(_first(v, "frequency", "priceFrequency", "rentFrequency") or "").lower() or None
        amt = _first(v, "amount", "value", "price", "internalValue", "priceValue")
        if amt is None:
            dp = v.get("displayPrices") or v.get("displayPrice") or v.get("label")
            if isinstance(dp, list) and dp:
                dp = dp[0].get("displayPrice") if isinstance(dp[0], dict) else dp[0]
            amt_f, f2 = parse_money(dp)
            return amt_f, freq or f2
        amt_f, f2 = parse_money(amt)
        if freq and "week" in freq:
            freq = "weekly"
        elif freq and "month" in freq:
            freq = "monthly"
        return amt_f, freq or f2
    s = str(v)
    m = PRICE_RE.search(s) or re.search(r"([\d,]{4,})", s)
    if not m:
        return None, None
    try:
        amt = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, None
    unit = (m.group(2) or "").lower() if m.lastindex and m.lastindex >= 2 else ""
    freq = "weekly" if unit and "w" in unit else "monthly" if unit else None
    return amt, freq


def _address(v: Any) -> str:
    if isinstance(v, dict):
        parts = [v.get(k) for k in ("displayAddress", "streetAddress", "addressLocality", "postalCode",
                                    "line1", "town", "postcode") if v.get(k)]
        return ", ".join(str(p) for p in parts)
    return str(v or "").strip()


def _images(d: dict) -> list[str]:
    urls: list[str] = []
    pi = d.get("propertyImages")
    if isinstance(pi, dict):
        for im in pi.get("images") or []:
            u = im.get("srcUrl") or im.get("url")
            if u:
                urls.append(u)
        if pi.get("mainImageSrc"):
            urls.insert(0, pi["mainImageSrc"])
    for key in ("images", "image", "photos", "imageUris"):
        v = d.get(key)
        if isinstance(v, str):
            urls.append(v)
        elif isinstance(v, list):
            for im in v:
                if isinstance(im, str):
                    urls.append(im)
                elif isinstance(im, dict):
                    u = im.get("url") or im.get("srcUrl") or im.get("src") or im.get("original")
                    if u:
                        urls.append(u)
    seen, out = set(), []
    for u in urls:
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("http") and u not in seen:
            seen.add(u)
            out.append(u)
    return out[:12]


_DATE_RE = re.compile(r"\b(added|reduced)\b[^0-9a-z]{0,12}(?:on\s+)?(\d{1,2}/\d{1,2}/\d{4}|today|yesterday)", re.I)


def listing_dates(text: str, first_visible: str | None = None) -> tuple[str | None, str | None]:
    """(listed_on, reduced_on) as ISO dates, from portal wording like 'Reduced on 12/11/2026'."""
    from datetime import date, timedelta

    listed = first_visible[:10] if first_visible else None
    reduced = None
    for kind, when in _DATE_RE.findall(text or ""):
        w = when.lower()
        if w == "today":
            iso = date.today().isoformat()
        elif w == "yesterday":
            iso = (date.today() - timedelta(days=1)).isoformat()
        else:
            dd, mm, yy = w.split("/")
            iso = f"{yy}-{int(mm):02d}-{int(dd):02d}"
        if kind.lower() == "reduced":
            reduced = reduced or iso
        else:
            listed = listed or iso
    return listed, reduced


def normalise_json_listing(d: dict, base_url: str, channel: str) -> dict | None:
    lid = _first(d, *_ID_KEYS)
    price, freq = parse_money(_first(d, *_PRICE_KEYS))
    if not lid or not price:
        return None
    if channel == "rent" and freq == "weekly":
        price = price * 52 / 12
    url = _first(d, "propertyUrl", "detailUrl", "details-url", "url", "listingUrl", "uri")
    if isinstance(d.get("listingUris"), dict):
        url = url or d["listingUris"].get("detail")
    addr = _address(_first(d, "displayAddress", "address", "displayable_address", "addressLine",
                           "streetAddress", "title"))
    type_text = " ".join(str(x) for x in [
        _first(d, "propertySubType", "propertyType", "propertyTypeFullDescription", "property_type",
               "propertyTypeDisplay", "humanised-property-type"), d.get("title"), d.get("property-title"),
        d.get("summary")] if x)
    beds = _first(d, "bedrooms", "numBedrooms", "num_beds", "beds", "numberOfBedrooms")
    try:
        beds = int(beds) if beds is not None else None
    except (TypeError, ValueError):
        beds = None
    loc = d.get("location") if isinstance(d.get("location"), dict) else {}
    summary = str(_first(d, "summary", "description", "shortDescription", "propertyDescription") or "")
    if not summary and isinstance(d.get("features"), list):  # OnTheMarket: tenure etc. live in features
        summary = ". ".join(str(f) for f in d["features"] if f)
    text_all = f"{type_text} {summary} {d.get('displayStatus') or ''}".lower()
    upd = d.get("listingUpdate") if isinstance(d.get("listingUpdate"), dict) else {}
    date_text = " ".join(str(x) for x in (d.get("addedOrReduced"), d.get("addedOn"), d.get("addedOrReducedDate")) if x)
    reason = (upd.get("listingUpdateReason") or "").lower()
    if reason.startswith(("price_reduced", "reduced")) and upd.get("listingUpdateDate"):
        date_text += " reduced on " + "/".join(reversed(str(upd["listingUpdateDate"])[:10].split("-")))
    listed_on, reduced_on = listing_dates(date_text, _first(d, "firstVisibleDate", "listedDate", "publishedOn"))
    return {
        "listed_on": listed_on, "reduced_on": reduced_on,
        "id": str(lid),
        "url": urljoin(base_url, url) if url else None,
        "price": round(price, 2),
        "address": addr,
        "bedrooms": beds,
        "property_type": normalise_type(type_text) if type_text else "unknown",
        "summary": htmllib.unescape(re.sub(r"<[^>]+>", " ", summary)).strip(),
        "images": _images(d),
        "lat": loc.get("latitude") or loc.get("lat"),
        "lng": loc.get("longitude") or loc.get("lng") or loc.get("lon"),
        "is_auction": bool(d.get("auction")) or "auction" in text_all,
        "sstc": "sold stc" in text_all or "under offer" in text_all or bool(d.get("sstc")),
    }


def from_json_ld(obj: Any, base_url: str, channel: str) -> list[dict]:
    items: list[dict] = []
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(cur)
            continue
        if not isinstance(cur, dict):
            continue
        if "@graph" in cur:
            stack.append(cur["@graph"])
        if "itemListElement" in cur:
            stack.extend(e.get("item", e) if isinstance(e, dict) else e for e in cur["itemListElement"])
        offers = cur.get("offers")
        url = cur.get("url") or (offers.get("url") if isinstance(offers, dict) else None)
        price = None
        if isinstance(offers, dict):
            price, _ = parse_money(offers.get("price"))
        if price and url:
            m = re.search(r"(\d{5,})", url)
            if not m:
                continue
            addr = _address(cur.get("address") or cur.get("name"))
            beds = cur.get("numberOfBedrooms") or cur.get("numberOfRooms")
            items.append({
                "id": m.group(1), "url": urljoin(base_url, url), "price": price, "address": addr,
                "bedrooms": int(beds) if str(beds or "").isdigit() else None,
                "property_type": normalise_type(f"{cur.get('@type', '')} {cur.get('name', '')}"),
                "summary": str(cur.get("description") or ""), "images": _images(cur),
                "lat": None, "lng": None, "is_auction": False, "sstc": False,
            })
    return items


# ---------------------------------------------------------------------------
# DOM cards
# ---------------------------------------------------------------------------


def parse_card_text(text: str, url: str, lid: str, channel: str) -> dict | None:
    """Read a listing card's visible text."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    price, freq = None, None
    for ln in lines:
        if "£" in ln and not re.search(r"(reduced|was|stamp duty|deposit|per sq)", ln, re.I):
            price, freq = parse_money(ln)
            if price:
                break
    if not price:
        return None
    if channel == "rent" and freq == "weekly":
        price = price * 52 / 12
    beds_m = BEDS_RE.search(text)
    addr = ""
    for ln in lines:
        if "£" in ln or len(ln) < 8 or BEDS_RE.search(ln):
            continue
        if "," in ln or POSTCODE_RE.search(ln):
            addr = ln
            break
    low = text.lower()
    listed_on, reduced_on = listing_dates(text)
    return {
        "listed_on": listed_on, "reduced_on": reduced_on,
        "id": lid, "url": url, "price": round(price, 2), "address": addr,
        "bedrooms": int(beds_m.group(1)) if beds_m else None,
        "property_type": normalise_type(text),
        "summary": " ".join(lines)[:600], "images": [], "lat": None, "lng": None,
        "is_auction": "auction" in low, "sstc": "sold stc" in low or "under offer" in low,
    }


def extract_listings(page_html: str, cards: list[dict], *, base_url: str, detail_re: re.Pattern,
                     channel: str) -> list[dict]:
    """Combine all three layers; JSON wins, DOM cards fill gaps. Keyed by portal id."""
    found: dict[str, dict] = {}
    for blob in json_blobs(page_html):
        for d in find_listing_dicts(blob):
            n = normalise_json_listing(d, base_url, channel)
            if n:
                found.setdefault(n["id"], n)
        for n in from_json_ld(blob, base_url, channel):
            found.setdefault(n["id"], n)
    # Keep only ids that correspond to a detail link when we have links (filters out
    # featured/agent widgets that also look like listings).
    linked_ids = {}
    for c in cards:
        m = detail_re.search(c.get("href") or "")
        if m:
            linked_ids.setdefault(m.group(1), c)
    if linked_ids and found:
        found = {k: v for k, v in found.items() if k in linked_ids} or found
    for lid, c in linked_ids.items():
        if lid not in found:
            n = parse_card_text(c.get("text") or "", urljoin(base_url, c["href"]), lid, channel)
            if n:
                found[lid] = n
        elif not found[lid].get("url"):
            found[lid]["url"] = urljoin(base_url, c["href"])
    return list(found.values())


# ---------------------------------------------------------------------------
# Detail pages
# ---------------------------------------------------------------------------


def _walk_strings(obj: Any, keys: set[str], out: list[str], depth: int = 0) -> None:
    if depth > 30:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str):
                out.append(v)
            else:
                _walk_strings(v, keys, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _walk_strings(v, keys, out, depth + 1)


def _walk_lists(obj: Any, keys: set[str], out: list[Any], depth: int = 0) -> None:
    if depth > 30:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, list):
                out.extend(v)
            else:
                _walk_lists(v, keys, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _walk_lists(v, keys, out, depth + 1)


def extract_detail(page_html: str, page_text: str) -> dict:
    """Description, key features, tenure, lease, floor area, images and floorplans."""
    blobs = json_blobs(page_html)
    descs: list[str] = []
    feats: list[Any] = []
    tenure_vals: list[str] = []
    plans: list[Any] = []
    imgs: list[str] = []
    for b in blobs:
        _walk_strings(b, {"description", "fullDescription", "detailedDescription",
                          "propertyDescription"}, descs)
        _walk_lists(b, {"keyFeatures", "features", "bullets", "feature_list"}, feats)
        _walk_strings(b, {"tenureType", "tenure"}, tenure_vals)
        _walk_lists(b, {"floorplans", "floorPlans", "floorplan"}, plans)
        for d in find_listing_dicts(b) or ([b] if isinstance(b, dict) else []):
            imgs += _images(d)
        pd = b.get("propertyData") if isinstance(b, dict) else None
        if isinstance(pd, dict):
            imgs += [i.get("url") for i in pd.get("images") or [] if isinstance(i, dict) and i.get("url")]
    clean = lambda s: htmllib.unescape(re.sub(r"<[^>]+>", " ", s)).strip()  # noqa: E731
    description = clean(max(descs, key=len)) if descs else ""
    features = [clean(f if isinstance(f, str) else str(f.get("text") or f.get("description") or ""))
                for f in feats]
    features = [f for f in features if f][:15]
    if len(description) < 80:  # fall back to the visible page text
        m = re.search(r"(?:description|about this property)\s*\n(.{80,4000}?)(?:\n\s*\n|$)", page_text,
                      re.I | re.S)
        if m:
            description = m.group(1).strip()
    blob_text = f"{description}\n{' '.join(features)}\n{' '.join(tenure_vals)}\n{page_text[:20000]}"
    sqft = None
    m = SQFT_RE.search(blob_text)
    if m:
        sqft = float(m.group(1).replace(",", ""))
    else:
        m = SQM_RE.search(blob_text)
        if m:
            try:
                sqft = round(float(m.group(1).replace(",", "")) * 10.7639, 1)
            except ValueError:
                sqft = None
    if sqft is not None and not 150 <= sqft <= 10000:
        sqft = None
    lease = LEASE_RE.search(blob_text)
    plan_urls = []
    for p in plans:
        u = p if isinstance(p, str) else (p.get("url") or p.get("src") or p.get("original")
                                          if isinstance(p, dict) else None)
        if u:
            plan_urls.append("https:" + u if u.startswith("//") else u)
    tenure_text = " ".join(tenure_vals).lower() or blob_text.lower()
    tenure = ("share-of-freehold" if "share of freehold" in tenure_text
              else "leasehold" if "leasehold" in tenure_text
              else "freehold" if "freehold" in tenure_text else None)
    return {
        "description": "\n".join([description, *(f"• {f}" for f in features)]).strip(),
        "tenure": tenure,
        "lease_years": int(lease.group(1)) if lease else None,
        "floor_area_sqft": sqft,
        "images": list(dict.fromkeys(u for u in imgs if u and u.startswith("http")))[:10],
        "floorplans": list(dict.fromkeys(plan_urls))[:3],
    }
