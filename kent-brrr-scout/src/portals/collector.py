"""Daily portal pass: newest for-sale listings (+ weekly rentals for comps) per outcode."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ..ingestion import category_for_type, infer_tenure
from ..models import RawListing, Tenure
from ..storage import Storage
from .browser import PortalBrowser
from .extract import extract_detail, extract_listings
from .sites import Portal, build_portals

log = logging.getLogger(__name__)

Progress = Callable[..., None]

_FULL_PC = re.compile(r"\b((?:ME|CT)\d{1,2})\s+(\d[A-Z]{2})\b", re.I)


def _noop(*_a: Any, **_k: Any) -> None:
    pass


def to_raw_listing(item: dict, portal: Portal, outcode: str) -> RawListing:
    pc = _FULL_PC.search(item.get("address") or "")
    postcode = f"{pc.group(1).upper()} {pc.group(2).upper()}" if pc else outcode
    text = f"{item.get('address', '')} {item.get('summary', '')}"
    tenure, lease = infer_tenure(text)
    ptype = item.get("property_type") or "unknown"
    return RawListing(
        listing_id=f"{portal.prefix}-{item['id']}", source=portal.name, url=item.get("url"),
        address=item.get("address") or "", postcode=postcode, outcode=outcode,
        asking_price=item["price"], property_type=ptype, category=category_for_type(ptype, text),
        tenure=tenure, lease_years_remaining=lease, bedrooms=item.get("bedrooms"),
        description=item.get("summary") or "", image_urls=item.get("images") or [],
        is_auction=bool(item.get("is_auction")), lat=item.get("lat"), lng=item.get("lng"),
    )


def _address_key(lst: RawListing) -> str:
    a = re.sub(r"[^a-z0-9]", "", lst.address.lower().split(",")[0])
    return f"{lst.outcode}|{a}|{lst.bedrooms}|{round(lst.asking_price, -3)}"


class PortalSession:
    """Owns the browser for the whole run so detail pages can be fetched mid-pipeline."""

    def __init__(self, cfg: dict[str, Any], storage: Storage, progress: Progress | None = None):
        self.cfg = cfg
        self.pc = cfg["portals"]
        self.storage = storage
        self.progress = progress or _noop
        self.portals = {p.name: p for p in build_portals(cfg)}
        self.browser = PortalBrowser(cfg)
        self.status: dict[str, dict[str, Any]] = {
            n: {"state": "pending", "pages": 0, "listings": 0, "rentals": 0, "new": 0,
                "message": "", "debug": None} for n in self.portals
        }
        self.details_fetched = 0

    def __enter__(self) -> PortalSession:
        self.browser.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.browser.__exit__(*exc)
        self.storage.kv_set("portal_status", {
            "at": datetime.now(UTC).isoformat(timespec="seconds"), "portals": self.status})

    # -- search pages -------------------------------------------------------

    def _blocked(self, name: str, reason: str, res) -> None:
        st = self.status[name]
        st["state"] = "blocked"
        st["message"] = f"Stopped: {reason}. Try again later, or switch on 'show browser window'."
        st["debug"] = self.browser.dump_debug(f"{name}-blocked", res)
        log.warning("%s blocked (%s) — skipping it for this run", name, reason)

    def _pages(self, portal: Portal, outcode: str, channel: str, max_pages: int) -> list[dict]:
        st = self.status[portal.name]
        max_price = int(self.cfg["ingestion"].get("max_price", 350000))
        items: list[dict] = []
        seen_streak = 0
        for page in range(max_pages):
            url = portal.search_url(outcode, channel, page, max_price)
            self.progress("collect", f"{portal.name}: {outcode} {channel} page {page + 1}")
            res = self.browser.fetch(url, detail_pattern=portal.detail_re.pattern)
            st["pages"] += 1
            if res.blocked:
                self._blocked(portal.name, res.blocked, res)
                return items
            if res.error:
                st["message"] = f"{outcode} {channel}: {res.error[:160]}"
                break
            found = extract_listings(res.html, res.cards, base_url=portal.base_url,
                                     detail_re=portal.detail_re, channel=channel)
            if not found:
                if page == 0 and not st["debug"]:
                    st["debug"] = self.browser.dump_debug(f"{portal.name}-{outcode}-{channel}-empty", res)
                    st["message"] = (f"No listings read from {outcode} {channel} page — the site layout "
                                     f"may have changed (debug saved).")
                break
            fresh = [f for f in found if f["id"] not in {i["id"] for i in items}]
            if not fresh:
                break  # pagination looped back
            items += fresh
            if channel == "sale":
                for f in fresh:
                    known = self.storage.listing_price(f"{portal.prefix}-{f['id']}")
                    seen_streak = seen_streak + 1 if known and abs(known - f["price"]) < 1 else 0
                if seen_streak >= int(self.pc.get("stop_after_seen_streak", 12)):
                    break
            if len(found) < portal.page_size // 2:
                break  # last page
        return items

    def _rent_due(self, portal: Portal, outcode: str) -> bool:
        last = self.storage.kv_get(f"rent_refreshed:{portal.name}:{outcode}")
        if not last:
            return True
        days = int(self.pc.get("rent_refresh_days", 7))
        return datetime.fromisoformat(last) < datetime.now(UTC) - timedelta(days=days)

    def collect(self, outcodes: list[str]) -> list[RawListing]:
        listings: dict[str, RawListing] = {}
        by_address: set[str] = set()
        for name, portal in self.portals.items():
            st = self.status[name]
            st["state"] = "running"
            for oc in outcodes:
                if st["state"] == "blocked":
                    break
                if not portal.prepare(self.browser, oc, self.cfg, self.storage):
                    st["message"] = f"Could not resolve {oc} on {name}"
                    continue
                if st["state"] == "blocked":
                    break
                sale = self._pages(portal, oc, "sale", int(self.pc.get("max_sale_pages_per_outcode", 3)))
                market = []
                for item in sale:
                    lst = to_raw_listing(item, portal, oc)
                    market.append(self._market_row(portal, oc, "sale", lst))
                    if item.get("sstc"):
                        continue
                    key = _address_key(lst)
                    if lst.listing_id in listings or (lst.address and key in by_address):
                        continue  # same property already found on another portal
                    by_address.add(key)
                    listings[lst.listing_id] = lst
                st["listings"] += len(sale)
                st["new"] += self.storage.record_market(market)
                if st["state"] != "blocked" and self._rent_due(portal, oc):
                    rent = self._pages(portal, oc, "rent", int(self.pc.get("max_rent_pages_per_outcode", 2)))
                    rows = [self._market_row(portal, oc, "rent", to_raw_listing(i, portal, oc))
                            for i in rent]
                    self.storage.record_market(rows)
                    st["rentals"] += len(rent)
                    if rent:
                        self.storage.kv_set(f"rent_refreshed:{name}:{oc}",
                                            datetime.now(UTC).isoformat(timespec="seconds"))
            if st["state"] == "running":
                st["state"] = "ok" if st["listings"] else "empty"
        return list(listings.values())

    @staticmethod
    def _market_row(portal: Portal, outcode: str, channel: str, lst: RawListing) -> dict:
        return {"key": f"{channel}:{lst.listing_id}", "portal": portal.name, "channel": channel,
                "outcode": outcode, "property_type": lst.property_type, "bedrooms": lst.bedrooms,
                "price": lst.asking_price, "address": lst.address, "url": lst.url}

    # -- detail pages -------------------------------------------------------

    def fetch_detail(self, listing: RawListing) -> RawListing:
        """Fill description, tenure, lease, floor area and photos from the listing page."""
        portal = next((p for p in self.portals.values() if listing.source == p.name), None)
        limit = int(self.pc.get("max_detail_pages_per_run", 40))
        if not portal or not listing.url or self.details_fetched >= limit:
            return listing
        if self.status[portal.name]["state"] == "blocked":
            return listing
        self.details_fetched += 1
        self.progress("detail", f"{portal.name}: reading {listing.address or listing.listing_id}")
        res = self.browser.fetch(listing.url, cards=False)
        if res.blocked:
            self._blocked(portal.name, res.blocked, res)
            return listing
        if res.error:
            return listing
        d = extract_detail(res.html, res.text)
        upd: dict[str, Any] = {}
        if len(d["description"]) > len(listing.description):
            upd["description"] = d["description"][:6000]
        if d["tenure"] and listing.tenure == Tenure.UNKNOWN:
            upd["tenure"] = Tenure(d["tenure"])
        if d["lease_years"] and not listing.lease_years_remaining:
            upd["lease_years_remaining"] = d["lease_years"]
        if d["floor_area_sqft"] and not listing.floor_area_sqft:
            upd["floor_area_sqft"] = d["floor_area_sqft"]
        if d["images"]:
            upd["image_urls"] = d["images"]
        if d["floorplans"]:
            upd["floorplan_urls"] = d["floorplans"]
        if upd.get("description"):
            t2, l2 = infer_tenure(upd["description"])
            if "tenure" not in upd and listing.tenure == Tenure.UNKNOWN and t2 != Tenure.UNKNOWN:
                upd["tenure"] = t2
            if not listing.lease_years_remaining and l2 and "lease_years_remaining" not in upd:
                upd["lease_years_remaining"] = l2
        return listing.model_copy(update=upd)
