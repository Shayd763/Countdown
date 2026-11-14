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


def to_raw_listing(item: dict, portal: Portal, outcode: str, *, channel: str = "residential",
                   search_area: str = "", search_kind: str = "new") -> RawListing:
    from ..geo import parse_postcode

    full, oc = parse_postcode(item.get("address") or "")
    if outcode and not oc:
        oc = outcode
    postcode = full or oc
    outcode = oc
    text = f"{item.get('address', '')} {item.get('summary', '')}"
    tenure, lease = infer_tenure(text)
    ptype = item.get("property_type") or "unknown"
    category = category_for_type(ptype, text)
    if channel == "commercial" and category.value == "residential":
        # a commercial search: anything not obviously a home is commercial
        from ..models import PropertyCategory

        category = PropertyCategory.COMMERCIAL
        if ptype in ("unknown", "terraced", "detached"):
            ptype = "commercial"
    return RawListing(
        listing_id=f"{portal.prefix}-{item['id']}", source=portal.name, url=item.get("url"),
        address=item.get("address") or "", postcode=postcode, outcode=outcode,
        asking_price=item["price"], property_type=ptype, category=category,
        tenure=tenure, lease_years_remaining=lease, bedrooms=item.get("bedrooms"),
        description=item.get("summary") or "", image_urls=item.get("images") or [],
        is_auction=bool(item.get("is_auction")), lat=item.get("lat"), lng=item.get("lng"),
        search_area=search_area, search_kind=search_kind,
        listed_on=item.get("listed_on"), reduced_on=item.get("reduced_on"),
        days_on_market=_days_since(item.get("listed_on")),
    )


def _days_since(iso: str | None) -> int | None:
    from datetime import date

    try:
        return (date.today() - date.fromisoformat(iso[:10])).days if iso else None
    except ValueError:
        return None


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
        self.comps_fetched = 0
        self.located: dict[str, int] = {}

    def __enter__(self) -> PortalSession:
        self.browser.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.browser.__exit__(*exc)
        self.storage.kv_set("portal_status", {
            "at": datetime.now(UTC).isoformat(timespec="seconds"), "portals": self.status,
            "located": self.located, "comps_districts": self.comps_fetched})

    # -- search pages -------------------------------------------------------

    def _blocked(self, name: str, reason: str, res) -> None:
        st = self.status[name]
        st["state"] = "blocked"
        st["message"] = f"Stopped: {reason}. Try again later, or switch on 'show browser window'."
        st["debug"] = self.browser.dump_debug(f"{name}-blocked", res)
        log.warning("%s blocked (%s) — skipping it for this run", name, reason)

    def _pages(self, portal: Portal, outcode: str, channel: str, max_pages: int,
               url_for=None, label: str | None = None, stop_at_seen: bool = True) -> list[dict]:
        """Fetch search pages newest-first. `channel` is the extraction channel (sale/rent);
        `url_for(page)` overrides the outcode URL (county searches)."""
        st = self.status[portal.name]
        max_price = int(self.cfg["ingestion"].get("max_price", 350000))
        items: list[dict] = []
        seen_streak = 0
        for page in range(max_pages):
            url = url_for(page) if url_for else portal.search_url(outcode, channel, page, max_price)
            if not url:
                break
            self.progress("collect", f"{portal.name}: {label or outcode} {channel} page {page + 1}")
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
                if page == 0 and not st["debug"] and not url_for:
                    st["debug"] = self.browser.dump_debug(f"{portal.name}-{outcode}-{channel}-empty", res)
                    st["message"] = (f"No listings read from {outcode} {channel} page — the site layout "
                                     f"may have changed (debug saved).")
                if page == 0 and url_for:
                    st.setdefault("empty_searches", 0)
                    st["empty_searches"] += 1
                break
            fresh = [f for f in found if f["id"] not in {i["id"] for i in items}]
            if not fresh:
                break  # pagination looped back
            items += fresh
            if channel == "sale":
                for f in fresh:
                    known = self.storage.listing_price(f"{portal.prefix}-{f['id']}")
                    seen_streak = seen_streak + 1 if known and abs(known - f["price"]) < 1 else 0
                if stop_at_seen and seen_streak >= int(self.pc.get("stop_after_seen_streak", 12)):
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

    def collect_areas(self, areas: list[str]) -> list[RawListing]:
        """England-wide: newest residential and commercial listings for each county search."""
        cov = self.cfg.get("coverage") or {}
        channels = cov.get("channels") or ["residential", "commercial"]
        days = int(cov.get("added_within_days", 1))
        max_pages = int(cov.get("max_pages_per_search", 8))
        keywords = [k for k in (cov.get("keywords") or []) if k] or [""]
        ing = self.cfg["ingestion"]
        prices = {"residential": int(ing.get("max_price", 350000)),
                  "commercial": int(ing.get("max_price_commercial", ing.get("max_price", 350000)))}
        listings: dict[str, RawListing] = {}
        by_address: set[str] = set()
        for name, portal in self.portals.items():
            st = self.status[name]
            st["state"] = "running"
            st.setdefault("areas", 0)
            for area in areas:
                if st["state"] == "blocked":
                    break
                if not portal.prepare_area(self.browser, area, self.cfg, self.storage):
                    st["message"] = f"Could not find {area} on {name}"
                    continue
                st["areas"] += 1
                for ch in channels:
                    for kw in (keywords if ch == "residential" else [""]):
                        if st["state"] == "blocked":
                            break
                        items = self._pages(
                            portal, "", "sale", max_pages, label=f"{area} {ch}{' ' + kw if kw else ''}",
                            url_for=lambda pg, a=area, c=ch, k=kw, pt=portal: pt.area_search_url(
                                self.cfg, a, c, pg, prices[c], days, k))
                        st["listings"] += len(items)
                        for item in items:
                            if item.get("sstc"):
                                continue
                            lst = to_raw_listing(item, portal, "", channel=ch, search_area=area, search_kind="new")
                            key = _address_key(lst)
                            if lst.listing_id in listings or (lst.address and key in by_address):
                                continue
                            by_address.add(key)
                            listings[lst.listing_id] = lst
            if st["state"] == "running":
                st["state"] = "ok" if st["listings"] else "empty"
        self._found = listings
        self._by_address = by_address
        out = self.locate(list(listings.values()))
        return out

    def collect_extra(self, areas: list[str], kinds: list[str]) -> list[RawListing]:
        """Reduced-price, long-on-market and rolling-backfill searches (residential)."""
        from datetime import date

        cov = self.cfg.get("coverage") or {}
        searches = cov.get("searches") or {}
        max_price = int(self.cfg["ingestion"].get("max_price", 350000))
        found: dict[str, RawListing] = {}
        seen_ids = set(getattr(self, "_found", {}))
        by_address = set(getattr(self, "_by_address", set()))
        market: list[dict] = []

        def tonight(spec: dict) -> list[str]:
            n = max(1, int(spec.get("cycle_nights", 7)))
            ordered = sorted(areas)
            return ordered[date.today().toordinal() % n::n]

        plan: list[tuple[str, list[str], list[str], str, int]] = []  # (kind, areas, keywords, sort, pages)
        if "reduced" in kinds and (searches.get("reduced") or {}).get("enabled"):
            s = searches["reduced"]
            plan.append(("reduced", areas, [""], "newest", int(s.get("pages", 2))))
        if "stale" in kinds and (searches.get("stale") or {}).get("enabled"):
            s = searches["stale"]
            plan.append(("stale", tonight(s), [""], "oldest", int(s.get("pages", 2))))
        if "sweep" in kinds and (searches.get("sweep") or {}).get("enabled"):
            s = searches["sweep"]
            plan.append(("sweep", tonight(s), list(s.get("keywords") or [""]), "newest",
                         int(s.get("pages_per_search", 4))))

        today = date.today()
        for kind, kind_areas, keywords, sort_name, pages in plan:
            spec = searches.get(kind) or {}
            for name in spec.get("portals") or list(self.portals):
                portal = self.portals.get(name)
                if not portal:
                    continue
                st = self.status[name]
                urls = (self.cfg["portals"].get("area_urls") or {}).get(name) or {}
                tpl, sort_val = urls.get("residential_all"), (urls.get("sorts") or {}).get(sort_name)
                if not tpl or not sort_val:
                    continue
                st.setdefault("extra", {}).setdefault(kind, 0)
                for area in kind_areas:
                    if st["state"] == "blocked":
                        break
                    if not portal.prepare_area(self.browser, area, self.cfg, self.storage):
                        continue
                    for kw in keywords:
                        if st["state"] == "blocked":
                            break

                        def url_for(pg, a=area, k=kw, t=tpl, sv=sort_val, pt=portal):
                            from urllib.parse import quote, quote_plus

                            return t.format(base=pt.base_url, loc=quote(pt.area_loc(a), safe=""),
                                            max_price=max_price, sort=sv, index=pg * pt.page_size,
                                            page1=pg + 1, kw=f"&keywords={quote_plus(k)}" if k else "")

                        items = self._pages(portal, "", "sale", pages, url_for=url_for, stop_at_seen=False,
                                            label=f"{area} {kind}{' ' + kw if kw else ''}")
                        for item in items:
                            if item.get("sstc"):
                                continue
                            lst = to_raw_listing(item, portal, "", search_area=area, search_kind=kind)
                            if lst.outcode:
                                market.append(self._market_row(portal, lst.outcode, "sale", lst))
                            if kind == "reduced":
                                known = self.storage.listing_price(lst.listing_id)
                                window = int(spec.get("within_days", 2))
                                cut_age = (today - date.fromisoformat(lst.reduced_on)).days if lst.reduced_on else None
                                recent_cut = cut_age is not None and cut_age <= window
                                if not (recent_cut or (known and lst.asking_price < known - 1)):
                                    continue
                            if kind == "stale" and (lst.days_on_market or 0) < int(spec.get("min_days_on_market", 90)):
                                continue
                            key = _address_key(lst)
                            dup = lst.listing_id in seen_ids or lst.listing_id in found
                            if dup or (lst.address and key in by_address):
                                continue
                            by_address.add(key)
                            found[lst.listing_id] = lst
                            st["extra"][kind] += 1
        if market:
            self.storage.record_market(market)
        return self.locate(list(found.values()))

    def locate(self, listings: list[RawListing]) -> list[RawListing]:
        """Give every listing a postcode district (from its address, else its map position) and
        drop those outside coverage."""
        from ..geo import in_coverage, reverse_geocode

        need = [(lst.lat, lst.lng) for lst in listings if not lst.outcode and lst.lat and lst.lng]
        found = reverse_geocode(need, self.storage, (self.cfg.get("land_registry_ppd") or {}).get(
            "postcodes_io", "https://api.postcodes.io")) if need else {}
        kept, unplaced, outside = [], 0, 0
        for lst in listings:
            if not lst.outcode and lst.lat and lst.lng:
                oc = found.get((lst.lat, lst.lng))
                if oc:
                    lst = lst.model_copy(update={"outcode": oc, "postcode": lst.postcode or oc})
            if not lst.outcode:
                unplaced += 1
                continue
            if not in_coverage(lst.outcode, self.cfg):
                outside += 1
                continue
            kept.append(lst)
        self.located = {"kept": len(kept), "no_postcode": unplaced, "outside_coverage": outside}
        return kept

    def ensure_comps(self, listing: RawListing) -> bool:
        """One page of rentals and one of homes for sale in this listing's district, if local
        comps are thin and weren't fetched recently. Returns True if anything was fetched."""
        oc = listing.outcode
        if not oc or self.comps_fetched >= int(self.pc.get("comps_max_districts_per_run", 120)):
            return False
        lc = self.cfg.get("local_comps") or {}
        since = (datetime.now(UTC) - timedelta(days=int(lc.get("lookback_days", 120)))).isoformat(timespec="seconds")
        need_n = int(lc.get("min_samples", 3))
        todo = []
        for channel, cache_key in (("rent", "comps_rent_cache_days"), ("sale", "comps_sale_cache_days")):
            if len(self.storage.market_prices(oc, channel, None, listing.bedrooms, since)) >= need_n:
                continue
            last = self.storage.kv_get(f"comps_fetch:{channel}:{oc}")
            max_age = timedelta(days=int(self.pc.get(cache_key, 30)))
            if last and datetime.fromisoformat(last) > datetime.now(UTC) - max_age:
                continue
            todo.append(channel)
        if not todo:
            return False
        self.comps_fetched += 1
        for portal in self.portals.values():
            if self.status[portal.name]["state"] == "blocked":
                continue
            if not portal.prepare(self.browser, oc, self.cfg, self.storage):
                continue
            for channel in todo:
                items = self._pages(portal, oc, channel, 1)
                rows = [self._market_row(portal, oc, channel, to_raw_listing(i, portal, oc)) for i in items]
                self.storage.record_market(rows)
                self.storage.kv_set(f"comps_fetch:{channel}:{oc}", datetime.now(UTC).isoformat(timespec="seconds"))
            return True
        return False

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
