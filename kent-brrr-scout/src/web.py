"""Local web UI + JSON API for browsing underwritten deals by postcode.

    python -m src.web            # http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import load_config, load_dotenv
from .main import run_scan
from .models import Deal
from .storage import Storage

STATIC = Path(__file__).parent / "static"


def deal_row(d: Deal) -> dict[str, Any]:
    r, lst = d.result, d.listing
    return {
        "listing_id": lst.listing_id,
        "outcode": lst.outcode,
        "postcode": lst.postcode,
        "address": lst.address,
        "url": lst.url,
        "source": lst.source,
        "is_auction": lst.is_auction,
        "category": lst.category.value,
        "property_type": lst.property_type,
        "bedrooms": lst.bedrooms,
        "tenure": lst.tenure.value,
        "asking_price": r.asking_price,
        "conservative_arv": r.conservative_arv,
        "discount_pct": round((1 - r.asking_price / r.conservative_arv) * 100, 1)
        if r.conservative_arv else None,
        "max_offer_price": r.max_offer_price,
        "refurb_tier": r.refurb_tier,
        "refurb_cost": r.refurb_cost,
        "gross_yield": r.gross_yield,
        "net_monthly_cashflow": r.net_monthly_cashflow,
        "roce_pct": r.roce_pct,
        "capital_recycled_pct": r.capital_recycled_pct,
        "money_left_in": r.money_left_in,
        "all_in_pct_arv": round(r.all_in_cost / r.conservative_arv, 4) if r.conservative_arv else None,
        "passed": r.passed and r.eligible,
        "eligible": r.eligible,
        "gates": {g.name: g.passed for g in r.gates},
        "red_flags": len(d.refurb.red_flags),
        "price_drop_pct": d.price_drop_pct,
    }


class ScanRequest(BaseModel):
    outcodes: list[str] | None = None
    offline: bool = False
    use_claude: bool = True
    include_rss: bool = True


def create_app() -> FastAPI:
    load_dotenv()
    app = FastAPI(title="Kent BRRR Scout")

    def storage() -> Storage:
        return Storage(os.environ.get("BRRR_DB_PATH"))

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        cfg = load_config()
        return {
            "outcodes": cfg["outcodes"],
            "thresholds": cfg["thresholds"],
            "categories": ["residential", "commercial", "mixed-use"],
            "property_types": ["terraced", "end-terrace", "semi-detached", "flat", "maisonette",
                               "detached", "bungalow", "commercial", "mixed-use"],
            "live_ready": bool(os.environ.get("PROPERTYDATA_API_KEY")),
            "claude_ready": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }

    @app.get("/api/summary")
    def summary() -> list[dict[str, Any]]:
        return storage().outcode_summary()

    @app.get("/api/deals")
    def deals(
        outcode: list[str] | None = Query(None),
        category: list[str] | None = Query(None),
        ptype: list[str] | None = Query(None),
        passed_only: bool = False,
        include_ineligible: bool = False,
        min_roce: float | None = None,
        max_price: float | None = None,
        min_yield: float | None = None,
        sort: str = "roce_pct",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        rows = storage().query_deals(
            outcodes=outcode, categories=category, property_types=ptype, passed_only=passed_only,
            include_ineligible=include_ineligible, min_roce=min_roce, max_price=max_price,
            min_yield=min_yield, sort=sort, limit=limit,
        )
        return [deal_row(d) for d in rows]

    @app.get("/api/deals/{listing_id}")
    def deal_detail(listing_id: str) -> dict[str, Any]:
        st = storage()
        deal = st.get_deal(listing_id)
        if deal is None:
            raise HTTPException(404, "deal not found")
        return {**deal.model_dump(mode="json"), "price_history": st.price_history(listing_id)}

    @app.post("/api/scan")
    def scan(req: ScanRequest) -> dict[str, Any]:
        # Sync def → FastAPI runs it in a worker thread, so the UI stays responsive.
        s = run_scan(req.outcodes, offline=req.offline, dry_run=True, use_claude=req.use_claude,
                     include_rss=req.include_rss, storage=storage())
        return s.as_dict()

    @app.get("/api/audit")
    def audit(limit: int = 100) -> list[dict[str, Any]]:
        return storage().recent_audit(limit)

    return app


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(prog="brrr-web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
