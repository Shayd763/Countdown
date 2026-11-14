"""Per-portal URL building. Extraction itself is generic (see extract.py)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, quote_plus, urlencode

if TYPE_CHECKING:
    from .browser import PortalBrowser

log = logging.getLogger(__name__)


@dataclass
class Portal:
    name: str
    prefix: str
    base_url: str
    detail_re: re.Pattern
    page_size: int = 25

    def prepare(self, browser: PortalBrowser, outcode: str, cfg: dict[str, Any], storage) -> bool:
        return True

    def search_url(self, outcode: str, channel: str, page: int, max_price: int) -> str:
        raise NotImplementedError

    # -- county (area) searches, from config templates --------------------------
    def prepare_area(self, browser: PortalBrowser, area: str, cfg: dict[str, Any], storage) -> bool:
        return True

    def area_loc(self, area: str) -> str:
        return ""

    def area_search_url(self, cfg: dict[str, Any], area: str, channel: str, page: int, max_price: int,
                        days: int, keyword: str = "") -> str | None:
        tpl = ((cfg["portals"].get("area_urls") or {}).get(self.name) or {}).get(channel)
        if not tpl:
            return None
        return tpl.format(
            base=self.base_url, loc=quote(self.area_loc(area), safe=""), slug=slugify(area),
            name=quote_plus(area), max_price=max_price, index=page * self.page_size, page1=page + 1,
            days=days, zoopla_added="24_hours" if days <= 1 else ("3_days" if days <= 3 else "7_days"),
            otm_added="24-hours" if days <= 1 else ("3-days" if days <= 3 else "7-days"),
            kw=f"&keywords={quote_plus(keyword)}" if keyword else "",
        )


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class Rightmove(Portal):
    def __init__(self, base_url: str, typeahead_url: str):
        super().__init__("rightmove", "rm", base_url.rstrip("/"), re.compile(r"/properties/(\d+)"), 24)
        self.typeahead_url = typeahead_url
        self._ids: dict[str, str] = {}

    def prepare(self, browser, outcode, cfg, storage) -> bool:
        pinned = (cfg["portals"].get("rightmove_location_ids") or {}).get(outcode)
        cached = storage.kv_get(f"rightmove_locid:{outcode}")
        if pinned or cached:
            self._ids[outcode] = pinned or cached
            return True
        url = f"{self.typeahead_url}?{urlencode({'query': outcode, 'limit': 10, 'exclude': 'STREET'})}"
        res = browser.fetch(url, polite=True, cards=False)
        try:
            data = json.loads(res.text)
            for m in data.get("matches") or data.get("typeAheadLocations") or []:
                mtype = (m.get("type") or m.get("locationType") or "").upper()
                name = (m.get("displayName") or m.get("name") or "").upper().replace(" ", "")
                ident = m.get("locationIdentifier") or (f"OUTCODE^{m['id']}" if m.get("id") else None)
                if mtype == "OUTCODE" and name == outcode and ident:
                    self._ids[outcode] = ident
                    storage.kv_set(f"rightmove_locid:{outcode}", ident)
                    return True
        except (ValueError, KeyError, AttributeError):
            pass
        log.warning("Rightmove: could not resolve location id for %s — pin it in config "
                    "portals.rightmove_location_ids", outcode)
        return False

    def prepare_area(self, browser, area, cfg, storage) -> bool:
        pinned = (cfg["portals"].get("rightmove_location_ids") or {}).get(area)
        cached = storage.kv_get(f"rightmove_locid:{area}")
        if pinned or cached:
            self._ids[area] = pinned or cached
            return True
        url = f"{self.typeahead_url}?{urlencode({'query': area, 'limit': 10, 'exclude': 'STREET'})}"
        res = browser.fetch(url, polite=True, cards=False)
        try:
            data = json.loads(res.text)
            want = area.lower()
            for m in data.get("matches") or data.get("typeAheadLocations") or []:
                mtype = (m.get("type") or m.get("locationType") or "").upper()
                name = (m.get("displayName") or m.get("name") or "").lower()
                ident = m.get("locationIdentifier") or (f"{mtype}^{m['id']}" if m.get("id") and mtype else None)
                if mtype in ("REGION", "COUNTY") and ident and (name == want or name.startswith(want + ",")
                                                                 or name.startswith(want + " ")):
                    self._ids[area] = ident
                    storage.kv_set(f"rightmove_locid:{area}", ident)
                    return True
        except (ValueError, KeyError, AttributeError):
            pass
        log.warning("Rightmove: could not resolve area %s; pin it in portals.rightmove_location_ids", area)
        return False

    def area_loc(self, area: str) -> str:
        return self._ids.get(area, "")

    def search_url(self, outcode, channel, page, max_price):
        q = {"locationIdentifier": self._ids[outcode], "sortType": 6, "index": page * self.page_size,
             "radius": "0.0"}
        if channel == "sale":
            q |= {"maxPrice": max_price, "dontShow": "retirement,sharedOwnership", "includeSSTC": "false"}
            path = "property-for-sale"
        else:
            q |= {"includeLetAgreed": "false", "dontShow": "houseShare,retirement,student"}
            path = "property-to-rent"
        return f"{self.base_url}/{path}/find.html?{urlencode(q, quote_via=quote)}"


class Zoopla(Portal):
    def __init__(self, base_url: str):
        super().__init__("zoopla", "zp", base_url.rstrip("/"),
                         re.compile(r"/(?:for-sale|to-rent|new-homes)/(?:commercial/)?details/(\d+)"), 25)

    def search_url(self, outcode, channel, page, max_price):
        q: dict[str, Any] = {"q": outcode, "results_sort": "newest_listings", "search_source": "refine",
                             "pn": page + 1}
        if channel == "sale":
            q |= {"price_max": max_price, "is_retirement_home": "false",
                  "is_shared_ownership": "false"}
            path = "for-sale"
        else:
            q |= {"price_frequency": "per_month", "is_shared_accommodation": "false",
                  "is_student_accommodation": "false"}
            path = "to-rent"
        return f"{self.base_url}/{path}/property/{outcode.lower()}/?{urlencode(q)}"


class OnTheMarket(Portal):
    def __init__(self, base_url: str):
        super().__init__("onthemarket", "otm", base_url.rstrip("/"), re.compile(r"/details/(\d+)"), 30)

    def search_url(self, outcode, channel, page, max_price):
        q: dict[str, Any] = {"sort-field": "update_date"}
        if page:
            q["page"] = page + 1
        if channel == "sale":
            q |= {"max-price": max_price, "retirement": "false", "shared-ownership": "false"}
            path = "for-sale"
        else:
            path = "to-rent"
        return f"{self.base_url}/{path}/property/{outcode.lower()}/?{urlencode(q)}"


def build_portals(cfg: dict[str, Any]) -> list[Portal]:
    pc = cfg["portals"]
    b = pc["base_urls"]
    available = {
        "rightmove": lambda: Rightmove(b["rightmove"], b["rightmove_typeahead"]),
        "zoopla": lambda: Zoopla(b["zoopla"]),
        "onthemarket": lambda: OnTheMarket(b["onthemarket"]),
    }
    return [available[n]() for n in pc.get("enabled", []) if n in available]
