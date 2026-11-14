"""SQLite persistence: dedup on listing_id, price history / drop tracking, audit log."""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlite_utils

from .config import PROJECT_ROOT
from .models import Deal, RawListing


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str | Path | None = None):
        path = Path(path or os.environ.get("BRRR_DB_PATH") or PROJECT_ROOT / "data" / "brrr.db")
        if not path.is_absolute() and str(path) != ":memory:":
            path = PROJECT_ROOT / path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite_utils.Database(str(path) if str(path) != ":memory:" else None,
                                        memory=str(path) == ":memory:")
        if str(path) != ":memory:":
            # The UI reads while a scan writes (and the scheduled run is another process).
            self.db.conn.execute("PRAGMA busy_timeout = 15000")
            if self.db.journal_mode != "wal":
                self.db.enable_wal()
        self._init_schema()

    def _init_schema(self) -> None:
        db = self.db
        if "listings" not in db.table_names():
            db["listings"].create({
                "listing_id": str, "source": str, "outcode": str, "postcode": str, "address": str,
                "url": str, "property_type": str, "category": str, "bedrooms": int,
                "current_price": float, "first_price": float, "first_seen": str, "last_seen": str,
                "raw": str,
            }, pk="listing_id")
            db["listings"].create_index(["outcode"])
        if "price_history" not in db.table_names():
            db["price_history"].create(
                {"id": int, "listing_id": str, "price": float, "seen_at": str}, pk="id"
            )
            db["price_history"].create_index(["listing_id"])
        if "deals" not in db.table_names():
            db["deals"].create({
                "listing_id": str, "outcode": str, "category": str, "property_type": str,
                "bedrooms": int, "asking_price": float, "conservative_arv": float,
                "refurb_tier": str, "gross_yield": float, "net_monthly_cashflow": float,
                "capital_recycled_pct": float, "roce_pct": float, "roce_infinite": int,
                "max_offer_price": float, "passed": int, "eligible": int, "price_drop_pct": float,
                "alerted_at": str, "underwritten_at": str, "deal_json": str,
            }, pk="listing_id")
            db["deals"].create_index(["outcode", "passed"])
        if "audit_log" not in db.table_names():
            db["audit_log"].create(
                {"id": int, "ts": str, "event": str, "listing_id": str, "detail": str}, pk="id"
            )
        if "deal_status" not in db.table_names():
            db["deal_status"].create(
                {"listing_id": str, "status": str, "notes": str, "updated_at": str}, pk="listing_id"
            )
        if "market" not in db.table_names():
            # Every for-sale / to-rent listing the portal collector sees → local comps.
            db["market"].create({
                "key": str, "portal": str, "channel": str, "outcode": str, "property_type": str,
                "bedrooms": int, "price": float, "address": str, "url": str,
                "first_seen": str, "last_seen": str,
            }, pk="key")
            db["market"].create_index(["outcode", "channel", "property_type", "bedrooms"])
        if "kv" not in db.table_names():
            db["kv"].create({"key": str, "value": str, "updated_at": str}, pk="key")
        if "users" not in db.table_names():
            db["users"].create({
                "id": int, "email": str, "name": str, "pw_hash": str, "is_admin": int,
                "prefs": str, "created_at": str,
            }, pk="id")
            db["users"].create_index(["email"], unique=True)
        if "sessions" not in db.table_names():
            db["sessions"].create({"token_hash": str, "user_id": int, "created_at": str,
                                   "expires_at": str, "user_agent": str}, pk="token_hash")
        if "push_subs" not in db.table_names():
            db["push_subs"].create({"endpoint": str, "user_id": int, "p256dh": str, "auth": str,
                                    "user_agent": str, "created_at": str, "last_ok": str}, pk="endpoint")
        if "runs" not in db.table_names():
            db["runs"].create({
                "id": int, "started_at": str, "finished_at": str, "trigger": str,
                "sources": str, "status": str, "summary": str,
            }, pk="id")

    # -- listings ---------------------------------------------------------

    def upsert_listing(self, listing: RawListing) -> dict[str, Any]:
        """Insert or update a listing. Returns {'is_new', 'price_changed', 'drop_pct'}."""
        now = _now()
        table = self.db["listings"]
        try:
            existing = table.get(listing.listing_id)
        except sqlite_utils.db.NotFoundError:
            existing = None
        info = {"is_new": existing is None, "price_changed": False, "drop_pct": None}
        if existing is None:
            table.insert({
                "listing_id": listing.listing_id, "source": listing.source,
                "outcode": listing.outcode, "postcode": listing.postcode, "address": listing.address,
                "url": listing.url, "property_type": listing.property_type,
                "category": listing.category.value, "bedrooms": listing.bedrooms,
                "current_price": listing.asking_price, "first_price": listing.asking_price,
                "first_seen": now, "last_seen": now, "raw": listing.model_dump_json(),
            })
            self._price(listing.listing_id, listing.asking_price, now)
            self.audit("listing_new", listing.listing_id, f"£{listing.asking_price:,.0f}")
        else:
            old = existing["current_price"]
            upd = {"last_seen": now, "raw": listing.model_dump_json()}
            if abs(old - listing.asking_price) >= 1:
                info["price_changed"] = True
                upd["current_price"] = listing.asking_price
                self._price(listing.listing_id, listing.asking_price, now)
                self.audit("price_change", listing.listing_id,
                           f"£{old:,.0f} -> £{listing.asking_price:,.0f}")
            table.update(listing.listing_id, upd)
            first = existing["first_price"]
            if first and listing.asking_price < first:
                info["drop_pct"] = round((first - listing.asking_price) / first * 100, 2)
        return info

    def _price(self, listing_id: str, price: float, ts: str) -> None:
        self.db["price_history"].insert({"listing_id": listing_id, "price": price, "seen_at": ts})

    def price_history(self, listing_id: str) -> list[dict[str, Any]]:
        return list(self.db["price_history"].rows_where(
            "listing_id = ?", [listing_id], order_by="seen_at"))

    # -- deals ------------------------------------------------------------

    def save_deal(self, deal: Deal) -> None:
        r, lst = deal.result, deal.listing
        prior = None
        with contextlib.suppress(sqlite_utils.db.NotFoundError):
            prior = self.db["deals"].get(lst.listing_id)
        self.db["deals"].upsert({
            "listing_id": lst.listing_id, "outcode": lst.outcode, "category": lst.category.value,
            "property_type": lst.property_type, "bedrooms": lst.bedrooms,
            "asking_price": r.asking_price, "conservative_arv": r.conservative_arv,
            "refurb_tier": r.refurb_tier, "gross_yield": r.gross_yield,
            "net_monthly_cashflow": r.net_monthly_cashflow,
            "capital_recycled_pct": r.capital_recycled_pct,
            "roce_pct": r.roce_pct if r.roce_pct is not None else 1e9,
            "roce_infinite": int(r.roce_pct is None), "max_offer_price": r.max_offer_price,
            "passed": int(r.passed and r.eligible), "eligible": int(r.eligible),
            "price_drop_pct": deal.price_drop_pct,
            "alerted_at": prior["alerted_at"] if prior else None,
            "underwritten_at": r.underwritten_at.isoformat(), "deal_json": deal.model_dump_json(),
        }, pk="listing_id")
        self.audit("underwritten", lst.listing_id,
                   f"passed={r.passed} eligible={r.eligible} roce={r.roce_display}")

    def should_alert(self, deal: Deal) -> bool:
        """Alert once per deal, and again only if the price has dropped since."""
        try:
            row = self.db["deals"].get(deal.listing.listing_id)
        except sqlite_utils.db.NotFoundError:
            return True
        return row["alerted_at"] is None or bool(deal.price_drop_pct)

    def mark_alerted(self, listing_id: str) -> None:
        self.db["deals"].update(listing_id, {"alerted_at": _now()})
        self.audit("alerted", listing_id, "")

    def query_deals(self, **filters: Any) -> list[Deal]:
        return [row["deal"] for row in self.query_deal_rows(**filters)]

    def query_deal_rows(
        self, *, outcodes: list[str] | None = None, categories: list[str] | None = None,
        property_types: list[str] | None = None, passed_only: bool = False,
        include_ineligible: bool = False, min_roce: float | None = None,
        max_price: float | None = None, min_yield: float | None = None,
        statuses: list[str] | None = None, hide_rejected: bool = False,
        new_since: str | None = None, search: str | None = None,
        sort: str = "roce_pct", limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Deals plus pipeline status, notes and first-seen date."""
        where, params = ["1=1"], []
        if outcodes:
            where.append(f"d.outcode in ({','.join('?' * len(outcodes))})")
            params += [o.upper() for o in outcodes]
        if categories:
            where.append(f"d.category in ({','.join('?' * len(categories))})")
            params += categories
        if property_types:
            where.append(f"d.property_type in ({','.join('?' * len(property_types))})")
            params += property_types
        if passed_only:
            where.append("passed = 1")
        if not include_ineligible:
            where.append("eligible = 1")
        if min_roce is not None:
            where.append("roce_pct >= ?")
            params.append(min_roce)
        if max_price is not None:
            where.append("asking_price <= ?")
            params.append(max_price)
        if min_yield is not None:
            where.append("gross_yield >= ?")
            params.append(min_yield)
        if statuses:
            where.append(f"coalesce(s.status, 'new') in ({','.join('?' * len(statuses))})")
            params += statuses
        if hide_rejected:
            where.append("coalesce(s.status, 'new') != 'rejected'")
        if new_since:
            where.append("l.first_seen >= ?")
            params.append(new_since)
        if search:
            where.append("(l.address like ? or l.postcode like ?)")
            params += [f"%{search}%", f"%{search}%"]
        sort_cols = {
            "roce_pct": "roce_pct desc", "gross_yield": "gross_yield desc",
            "net_monthly_cashflow": "net_monthly_cashflow desc",
            "asking_price": "asking_price asc", "discount": "(conservative_arv - asking_price) desc",
            "price_drop_pct": "coalesce(price_drop_pct, 0) desc",
            "newest": "l.first_seen desc",
        }
        sql = f"""
            select d.deal_json, coalesce(s.status, 'new') as status, s.notes, l.first_seen
            from deals d
            left join deal_status s on s.listing_id = d.listing_id
            left join listings l on l.listing_id = d.listing_id
            where {" and ".join(where)}
            order by passed desc, {sort_cols.get(sort, 'roce_pct desc')}
            limit {int(limit)}"""
        return [
            {"deal": Deal.model_validate_json(r["deal_json"]), "status": r["status"],
             "notes": r["notes"], "first_seen": r["first_seen"]}
            for r in self.db.query(sql, params)
        ]

    def set_status(self, listing_id: str, status: str | None = None, notes: str | None = None) -> dict:
        cur = {"status": "new", "notes": ""}
        with contextlib.suppress(sqlite_utils.db.NotFoundError):
            cur = self.db["deal_status"].get(listing_id)
        row = {"listing_id": listing_id, "status": status or cur["status"],
               "notes": cur["notes"] if notes is None else notes, "updated_at": _now()}
        self.db["deal_status"].upsert(row, pk="listing_id")
        self.audit("status", listing_id, {"status": row["status"]})
        return row

    def get_status(self, listing_id: str) -> dict[str, Any]:
        try:
            return self.db["deal_status"].get(listing_id)
        except sqlite_utils.db.NotFoundError:
            return {"listing_id": listing_id, "status": "new", "notes": ""}

    def first_seen(self, listing_id: str) -> str | None:
        try:
            return self.db["listings"].get(listing_id)["first_seen"]
        except sqlite_utils.db.NotFoundError:
            return None

    def listing_price(self, listing_id: str) -> float | None:
        rows = list(self.db.query(
            "select current_price from listings where listing_id = ? limit 1", [listing_id]))
        return rows[0]["current_price"] if rows else None

    def has_listing(self, listing_id: str) -> bool:
        return bool(list(self.db.query(
            "select 1 from listings where listing_id = ? limit 1", [listing_id])))

    def get_deal(self, listing_id: str) -> Deal | None:
        try:
            return Deal.model_validate_json(self.db["deals"].get(listing_id)["deal_json"])
        except sqlite_utils.db.NotFoundError:
            return None

    def outcode_summary(self) -> list[dict[str, Any]]:
        return list(self.db.query("""
            select outcode, count(*) as listings, sum(passed) as passing,
                   round(avg(gross_yield) * 100, 2) as avg_yield_pct,
                   max(case when passed = 1 and roce_infinite = 0 then roce_pct end) as best_roce_pct,
                   sum(roce_infinite * passed) as infinite_roce_deals
            from deals where eligible = 1 group by outcode order by passing desc, outcode"""))

    # -- market snapshot (local comps) --------------------------------------

    def record_market(self, rows: list[dict[str, Any]]) -> int:
        """Upsert portal-seen listings. Each row: key, portal, channel, outcode, property_type,
        bedrooms, price, address, url. Returns how many were new."""
        now, new = _now(), 0
        table = self.db["market"]
        for r in rows:
            try:
                existing = table.get(r["key"])
                table.update(r["key"], {**r, "last_seen": now, "first_seen": existing["first_seen"]})
            except sqlite_utils.db.NotFoundError:
                table.insert({**r, "first_seen": now, "last_seen": now})
                new += 1
        return new

    def market_prices(self, outcode: str, channel: str, property_type: str | None,
                      bedrooms: int | None, since: str) -> list[float]:
        where = ["outcode = ?", "channel = ?", "last_seen >= ?", "price > 0"]
        params: list[Any] = [outcode.upper(), channel, since]
        if property_type:
            types = {"terraced": ["terraced", "end-terrace"], "end-terrace": ["terraced", "end-terrace"],
                     "flat": ["flat", "maisonette"], "maisonette": ["flat", "maisonette"]}.get(
                property_type, [property_type])
            where.append(f"property_type in ({','.join('?' * len(types))})")
            params += types
        if bedrooms is not None:
            where.append("bedrooms = ?")
            params.append(bedrooms)
        return [r["price"] for r in self.db.query(
            f"select price from market where {' and '.join(where)}", params)]

    def market_stats(self) -> list[dict[str, Any]]:
        return list(self.db.query("""
            select outcode, channel, count(*) as n, max(last_seen) as last_seen
            from market group by outcode, channel order by outcode, channel"""))

    # -- key/value + runs ---------------------------------------------------

    def kv_get(self, key: str, default: Any = None) -> Any:
        try:
            return json.loads(self.db["kv"].get(key)["value"])
        except sqlite_utils.db.NotFoundError:
            return default

    def kv_set(self, key: str, value: Any) -> None:
        self.db["kv"].upsert({"key": key, "value": json.dumps(value, default=str),
                              "updated_at": _now()}, pk="key")

    def start_run(self, trigger: str, sources: list[str]) -> int:
        return self.db["runs"].insert({"started_at": _now(), "trigger": trigger,
                                       "sources": ",".join(sources), "status": "running"}).last_pk

    def finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        self.db["runs"].update(run_id, {"finished_at": _now(), "status": status,
                                        "summary": json.dumps(summary, default=str)})

    def recent_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = list(self.db["runs"].rows_where(order_by="id desc", limit=limit))
        for r in rows:
            r["summary"] = json.loads(r["summary"]) if r.get("summary") else None
        return rows

    # -- audit ------------------------------------------------------------

    def audit(self, event: str, listing_id: str | None, detail: str | dict) -> None:
        if isinstance(detail, dict):
            detail = json.dumps(detail, default=str)
        self.db["audit_log"].insert(
            {"ts": _now(), "event": event, "listing_id": listing_id, "detail": detail})

    def recent_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.db["audit_log"].rows_where(order_by="id desc", limit=limit))
