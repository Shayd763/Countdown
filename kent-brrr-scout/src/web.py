"""Local web app + JSON API.

    python -m src.web            # http://127.0.0.1:8000  (the start scripts open it for you)
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import threading
import time
import uuid
import webbrowser
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from .config import (
    SECRET_KEYS,
    all_outcodes,
    config_for_outcode,
    load_config,
    load_dotenv,
    save_env,
    save_user_overrides,
)
from .models import Deal, RefurbTier
from .storage import Storage
from .underwriting import UnderwritingParams, estimate_refurb_cost, underwrite

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("brrr.web")

STATUSES = ["new", "shortlisted", "viewing", "offer", "rejected"]


def deal_row(d: Deal, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    r, lst = d.result, d.listing
    meta = meta or {}
    return {
        "listing_id": lst.listing_id, "outcode": lst.outcode, "postcode": lst.postcode,
        "address": lst.address, "url": lst.url, "source": lst.source, "is_auction": lst.is_auction,
        "category": lst.category.value, "property_type": lst.property_type, "bedrooms": lst.bedrooms,
        "tenure": lst.tenure.value, "asking_price": r.asking_price, "conservative_arv": r.conservative_arv,
        "discount_pct": round((1 - r.asking_price / r.conservative_arv) * 100, 1)
        if r.conservative_arv else None,
        "max_offer_price": r.max_offer_price, "refurb_tier": r.refurb_tier, "refurb_cost": r.refurb_cost,
        "gross_yield": r.gross_yield, "net_monthly_cashflow": r.net_monthly_cashflow,
        "roce_pct": r.roce_pct, "capital_recycled_pct": r.capital_recycled_pct,
        "money_left_in": r.money_left_in,
        "all_in_pct_arv": round(r.all_in_cost / r.conservative_arv, 4) if r.conservative_arv else None,
        "passed": r.passed and r.eligible, "eligible": r.eligible,
        "gates": {g.name: g.passed for g in r.gates}, "red_flags": len(d.refurb.red_flags),
        "price_drop_pct": d.price_drop_pct, "image": (lst.image_urls or [None])[0],
        "status": meta.get("status", "new"), "notes": meta.get("notes") or "",
        "first_seen": meta.get("first_seen"),
    }


# ---------------------------------------------------------------------------
# Background job runner (one job at a time)
# ---------------------------------------------------------------------------


class Jobs:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.current: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any] | None:
        with self.lock:
            if not self.current:
                return None
            c = dict(self.current)
            c["log"] = list(c["log"])
            return c

    def start(self, kind: str, fn, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            if self.current and self.current["running"]:
                raise HTTPException(409, "A job is already running")
            job = {"id": uuid.uuid4().hex[:8], "kind": kind, "running": True, "phase": "starting",
                   "message": "Starting…", "done": 0, "total": 0, "error": None, "result": None,
                   "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                   "finished_at": None, "log": deque(maxlen=80)}
            self.current = job

        def progress(phase: str, message: str, **counts: Any) -> None:
            with self.lock:
                job["phase"], job["message"] = phase, message
                job["done"] = counts.get("done", job["done"])
                job["total"] = counts.get("total", job["total"])
                job["log"].append(f"{time.strftime('%H:%M:%S')} {message}")

        def runner() -> None:
            try:
                res = fn(progress=progress, **kwargs)
                job["result"] = res.as_dict() if hasattr(res, "as_dict") else res
            except Exception as exc:  # noqa: BLE001
                log.exception("job %s failed", kind)
                job["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                with self.lock:
                    job["running"] = False
                    job["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")

        threading.Thread(target=runner, daemon=True, name=f"job-{kind}").start()
        return {"id": job["id"], "kind": kind}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    outcodes: list[str] | None = None
    sources: list[str] | None = None
    offline: bool = False  # legacy alias for sources=["sample"]
    use_claude: bool = True
    send_alerts: bool = False


class StatusUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None


class WhatIf(BaseModel):
    asking_price: float | None = None
    refurb_tier: RefurbTier | None = None
    refurb_cost: float | None = None
    monthly_rent: float | None = None
    arv: float | None = None


class SettingsUpdate(BaseModel):
    secrets: dict[str, str] = {}
    config: dict[str, Any] = {}


# Which config keys the Settings page may change (section -> keys).
EDITABLE = {
    "thresholds": ["all_in_cap_pct_of_arv", "min_gross_yield", "min_net_monthly_cashflow",
                   "min_capital_recycled_pct", "min_roce_pct"],
    "finance": ["refi_ltv", "btl_interest_rate", "operating_cost_drag_pct", "corporation_tax_rate",
                "use_bridging", "bridging_monthly_rate", "bridging_months", "refi_fixed_costs",
                "refi_fee_pct_of_loan"],
    "costs": ["fixed_acquisition_costs", "arv_haircut_pct", "rent_haircut_pct", "refurb_contingency_pct",
              "refurb_per_sqft"],
    "ingestion": ["max_price"],
    "portals": ["enabled", "headless", "max_sale_pages_per_outcode"],
    "schedule": ["time", "sources"],
    "eligibility": ["include_commercial", "min_lease_years_flats"],
}


def _mask(v: str | None) -> dict[str, Any]:
    if not v:
        return {"set": False, "hint": ""}
    return {"set": True, "hint": ("…" + v[-4:]) if len(v) > 8 else "set"}


def create_app() -> FastAPI:
    load_dotenv()
    app = FastAPI(title="Kent BRRR Scout")
    jobs = Jobs()

    def storage() -> Storage:
        return Storage(os.environ.get("BRRR_DB_PATH"))

    def filters(outcode, category, ptype, passed_only, include_ineligible, min_roce, max_price,
                min_yield, status, hide_rejected, new_only, q, sort, limit) -> dict[str, Any]:
        new_since = None
        if new_only:
            runs = [r for r in storage().recent_runs(5) if r["status"] != "running"]
            new_since = runs[0]["started_at"] if runs else None
        return dict(outcodes=outcode, categories=category, property_types=ptype,
                    passed_only=passed_only, include_ineligible=include_ineligible, min_roce=min_roce,
                    max_price=max_price, min_yield=min_yield, statuses=status,
                    hide_rejected=hide_rejected, new_since=new_since, search=q, sort=sort, limit=limit)

    # -- pages ----------------------------------------------------------------

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    # -- metadata & summaries ---------------------------------------------------

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        cfg = load_config()
        st = storage()
        runs = st.recent_runs(1)
        return {
            "outcodes": cfg["outcodes"], "thresholds": cfg["thresholds"],
            "categories": ["residential", "commercial", "mixed-use"],
            "property_types": ["terraced", "end-terrace", "semi-detached", "flat", "maisonette",
                               "detached", "bungalow", "commercial", "mixed-use"],
            "statuses": STATUSES,
            "keys": {k: bool(os.environ.get(k)) for k in SECRET_KEYS},
            "schedule_time": cfg.get("schedule", {}).get("time"),
            "last_run": runs[0] if runs else None,
            "deal_count": st.db["deals"].count,
            "portal_status": st.kv_get("portal_status"),
        }

    @app.get("/api/summary")
    def summary() -> list[dict[str, Any]]:
        return storage().outcode_summary()

    # -- deals ------------------------------------------------------------------

    @app.get("/api/deals")
    def deals(
        outcode: list[str] | None = Query(None), category: list[str] | None = Query(None),
        ptype: list[str] | None = Query(None), passed_only: bool = False,
        include_ineligible: bool = False, min_roce: float | None = None,
        max_price: float | None = None, min_yield: float | None = None,
        status: list[str] | None = Query(None), hide_rejected: bool = False, new_only: bool = False,
        q: str | None = None, sort: str = "roce_pct", limit: int = 500,
    ) -> list[dict[str, Any]]:
        rows = storage().query_deal_rows(**filters(
            outcode, category, ptype, passed_only, include_ineligible, min_roce, max_price, min_yield,
            status, hide_rejected, new_only, q, sort, limit))
        return [deal_row(r["deal"], r) for r in rows]

    @app.get("/api/deals.csv")
    def deals_csv(
        outcode: list[str] | None = Query(None), category: list[str] | None = Query(None),
        ptype: list[str] | None = Query(None), passed_only: bool = False,
        include_ineligible: bool = False, min_roce: float | None = None,
        max_price: float | None = None, min_yield: float | None = None,
        status: list[str] | None = Query(None), hide_rejected: bool = False, new_only: bool = False,
        q: str | None = None, sort: str = "roce_pct",
    ) -> Response:
        rows = storage().query_deal_rows(**filters(
            outcode, category, ptype, passed_only, include_ineligible, min_roce, max_price, min_yield,
            status, hide_rejected, new_only, q, sort, 100_000))
        cols = ["listing_id", "status", "passed", "outcode", "postcode", "address", "property_type",
                "bedrooms", "tenure", "asking_price", "max_offer_price", "conservative_arv", "refurb_tier",
                "refurb_cost", "gross_yield", "net_monthly_cashflow", "roce_pct", "capital_recycled_pct",
                "money_left_in", "price_drop_pct", "first_seen", "url", "notes"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(deal_row(r["deal"], r))
        return Response(buf.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="brrr-deals-{datetime.now():%Y%m%d}.csv"'})

    @app.get("/api/deals/{listing_id}")
    def deal_detail(listing_id: str) -> dict[str, Any]:
        st = storage()
        deal = st.get_deal(listing_id)
        if deal is None:
            raise HTTPException(404, "deal not found")
        return {**deal.model_dump(mode="json"), "price_history": st.price_history(listing_id),
                "pipeline": st.get_status(listing_id), "first_seen": st.first_seen(listing_id)}

    @app.put("/api/deals/{listing_id}/status")
    def set_status(listing_id: str, body: StatusUpdate) -> dict[str, Any]:
        if body.status and body.status not in STATUSES:
            raise HTTPException(400, f"status must be one of {STATUSES}")
        return storage().set_status(listing_id, body.status, body.notes)

    @app.post("/api/deals/{listing_id}/whatif")
    def whatif(listing_id: str, body: WhatIf) -> dict[str, Any]:
        deal = storage().get_deal(listing_id)
        if deal is None:
            raise HTTPException(404, "deal not found")
        lst, comps = deal.listing, deal.comps
        p = UnderwritingParams.from_config(config_for_outcode(load_config(), lst.outcode))
        tier = body.refurb_tier or deal.refurb.tier
        res = underwrite(
            listing_id=lst.listing_id, outcode=lst.outcode,
            asking_price=body.asking_price or lst.asking_price,
            conservative_arv=body.arv or comps.conservative_arv,
            monthly_rent=body.monthly_rent or comps.conservative_monthly_rent,
            floor_area_sqft=comps.floor_area_sqft, refurb_tier=tier, params=p, category=lst.category,
            refurb_cost_override=body.refurb_cost,
        )
        out = res.model_dump(mode="json")
        out["tier_costs"] = {t: estimate_refurb_cost(t, comps.floor_area_sqft, p)
                             for t in ("light", "medium", "heavy", "structural")}
        return out

    # -- jobs -------------------------------------------------------------------

    @app.post("/api/scan")
    def scan(req: ScanRequest) -> dict[str, Any]:
        from .main import run_scan

        sources = ["sample"] if req.offline else req.sources
        return jobs.start("scan", run_scan, outcodes=req.outcodes, sources=sources,
                          dry_run=not req.send_alerts, use_claude=req.use_claude,
                          trigger="ui")  # storage is opened inside the worker thread

    @app.post("/api/rescore")
    def rescore() -> dict[str, Any]:
        from .main import rescore_all

        def fn(progress):
            progress("rescore", "Re-running the gates on saved deals with current settings…")
            return {"rescored": rescore_all(storage())}

        return jobs.start("rescore", fn)

    @app.post("/api/doctor")
    def doctor() -> dict[str, Any]:
        from .doctor import run_checks

        def fn(progress):
            progress("doctor", "Checking browser, portals, keys and data sources…")
            return {"checks": run_checks()}

        return jobs.start("doctor", fn)

    @app.get("/api/job")
    def job() -> dict[str, Any] | None:
        return jobs.snapshot()

    # -- runs, health, logs -------------------------------------------------------

    @app.get("/api/runs")
    def runs(limit: int = 30) -> list[dict[str, Any]]:
        return storage().recent_runs(limit)

    @app.get("/api/market")
    def market() -> list[dict[str, Any]]:
        return storage().market_stats()

    @app.get("/api/logs", response_class=PlainTextResponse)
    def logs(lines: int = 300) -> str:
        from .logs import tail

        return tail(lines)

    @app.get("/api/audit")
    def audit(limit: int = 100) -> list[dict[str, Any]]:
        return storage().recent_audit(limit)

    # -- settings -------------------------------------------------------------------

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        from .scheduler import status as sched_status

        cfg = load_config()
        return {
            "secrets": {k: _mask(os.environ.get(k)) for k in SECRET_KEYS},
            "config": {sec: {k: cfg.get(sec, {}).get(k) for k in keys} for sec, keys in EDITABLE.items()},
            "schedule": sched_status(),
            "all_outcodes": all_outcodes(cfg),
        }

    @app.put("/api/settings")
    def put_settings(body: SettingsUpdate) -> dict[str, Any]:
        secrets = {k: ("" if v == "__clear__" else v.strip()) for k, v in body.secrets.items()
                   if k in SECRET_KEYS and v and v.strip()}
        if secrets:
            save_env(secrets)
        cleaned: dict[str, Any] = {}
        for sec, vals in (body.config or {}).items():
            allowed = EDITABLE.get(sec)
            if not allowed or not isinstance(vals, dict):
                continue
            keep = {k: v for k, v in vals.items() if k in allowed}
            if keep:
                cleaned[sec] = keep
        if cleaned:
            save_user_overrides(cleaned)
        return {"saved_secrets": sorted(secrets), "saved_config": cleaned}

    @app.post("/api/schedule/install")
    def schedule_install(body: dict[str, Any] | None = None) -> dict[str, Any]:
        from .scheduler import install, status

        t = (body or {}).get("time") or load_config().get("schedule", {}).get("time", "07:30")
        try:
            msg = install(t)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, str(exc)) from exc
        save_user_overrides({"schedule": {"time": t}})
        return {"message": msg, "status": status()}

    @app.post("/api/schedule/remove")
    def schedule_remove() -> dict[str, Any]:
        from .scheduler import remove, status

        return {"message": remove(), "status": status()}

    @app.post("/api/test-alert")
    def test_alert() -> dict[str, Any]:
        from .notifier import Notifier

        n = Notifier()
        if not n.configured:
            raise HTTPException(400, "Add a Telegram bot token + chat id (or a webhook URL) first")
        st = storage()
        rows = st.query_deal_rows(passed_only=True, limit=1) or st.query_deal_rows(limit=1)
        if not rows:
            raise HTTPException(400, "No deals yet — run a scan (or load sample data) first")
        if not n.send(rows[0]["deal"]):
            raise HTTPException(502, "Sending failed — check the token / chat id (see Logs)")
        return {"message": "Test alert sent"}

    return app


def main() -> None:
    import uvicorn

    from .logs import setup_logging

    ap = argparse.ArgumentParser(prog="brrr-web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--open", action="store_true", help="Open the app in your browser")
    args = ap.parse_args()
    setup_logging()
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{args.host}:{args.port}/")).start()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
