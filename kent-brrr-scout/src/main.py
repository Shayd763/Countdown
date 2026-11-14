"""Orchestrator CLI.

    python -m src.main --sample --dry-run              # bundled sample data, no network
    python -m src.main --daily                         # the morning routine (portals + RSS + PropertyData)
    python -m src.main --sources portals --outcode ME4 # one outcode from the portals only
    python -m src.main --sources propertydata --min-roce 10
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from .claude_analyst import ClaudeAnalyst, heuristic_assessment, make_analyst
from .config import all_outcodes, config_for_outcode, load_config, load_dotenv
from .enrichment import enrich
from .http import DataClient, FixtureClient, PropertyDataClient
from .ingestion import check_eligibility, fetch_rss, fetch_sourced
from .models import Deal, RawListing, Tenure
from .notifier import Notifier
from .storage import Storage
from .underwriting import UnderwritingParams, underwrite

log = logging.getLogger("brrr")

ALL_SOURCES = ("portals", "rss", "propertydata", "sample")
Progress = Callable[..., None]


@dataclass
class RunSummary:
    outcodes: list[str]
    sources: list[str] = field(default_factory=list)
    fetched: int = 0
    pre_screen_rejected: int = 0
    underwritten: int = 0
    passed: int = 0
    new_passing: int = 0
    waiting_for_comps: int = 0
    alerted: int = 0
    claude_calls: int = 0
    details_fetched: int = 0
    comps_source: str = ""
    portals: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _noop(*_a: Any, **_k: Any) -> None:
    pass


def _apply_claude_tenure(listing: RawListing, refurb) -> RawListing:
    """Fill tenure/lease gaps from Claude's reading of the description (never overrides feed data)."""
    upd: dict[str, Any] = {}
    if listing.tenure == Tenure.UNKNOWN and refurb.detected_tenure != "unknown":
        upd["tenure"] = Tenure(refurb.detected_tenure)
    if listing.lease_years_remaining is None and refurb.detected_lease_years:
        upd["lease_years_remaining"] = refurb.detected_lease_years
    return listing.model_copy(update=upd) if upd else listing


def process_listing(
    listing: RawListing, *, client: DataClient, cfg: dict[str, Any], storage: Storage,
    analyst: ClaudeAnalyst | None, summary: RunSummary, use_external_fallbacks: bool,
    detail_fetcher: Callable[[RawListing], RawListing] | None = None,
) -> Deal | None:
    ocfg = config_for_outcode(cfg, listing.outcode)
    track = storage.upsert_listing(listing)

    ok, reasons = check_eligibility(listing, ocfg, allow_unknown_lease=True)
    if not ok:
        summary.pre_screen_rejected += 1
        storage.audit("pre_screen_reject", listing.listing_id, {"reasons": reasons})
        return None

    params = UnderwritingParams.from_config(ocfg)

    def evaluate(lst: RawListing):
        comps = enrich(lst, client, ocfg, use_external_fallbacks=use_external_fallbacks)
        if comps.conservative_arv <= 0 or comps.conservative_monthly_rent <= 0:
            return comps, None, None
        common = dict(
            listing_id=lst.listing_id, outcode=lst.outcode, asking_price=lst.asking_price,
            conservative_arv=comps.conservative_arv, monthly_rent=comps.conservative_monthly_rent,
            floor_area_sqft=comps.floor_area_sqft, params=params, category=lst.category,
        )
        return comps, common, underwrite(refurb_tier="light", **common)

    comps, common, best_case = evaluate(listing)
    if common is None:
        storage.audit("no_comps", listing.listing_id, {"sources": comps.sources})
        summary.waiting_for_comps += 1
        return None

    # Only listings that could still pass get a detail-page visit and a Claude call.
    if best_case.passed and detail_fetcher:
        detailed = detail_fetcher(listing)
        if detailed is not listing:
            summary.details_fetched += 1
            listing = detailed
            storage.upsert_listing(listing)
            ok, reasons = check_eligibility(listing, ocfg, allow_unknown_lease=True)
            if not ok:
                summary.pre_screen_rejected += 1
                storage.audit("pre_screen_reject", listing.listing_id, {"reasons": reasons})
                return None
            comps, common, best_case = evaluate(listing)
            if common is None:
                return None

    if analyst and best_case.passed:
        refurb = analyst.assess(listing)
        summary.claude_calls += 1
    else:
        refurb = heuristic_assessment(listing)

    listing = _apply_claude_tenure(listing, refurb)
    eligible, reasons = check_eligibility(listing, ocfg)
    if refurb.is_excluded_type:
        eligible = False
        reasons.append(f"Claude: {refurb.exclusion_reason or 'excluded property type'}")

    result = underwrite(refurb_tier=refurb.tier, **common)
    result.eligible, result.eligibility_reasons = eligible, reasons

    deal = Deal(listing=listing, comps=comps, refurb=refurb, result=result,
                price_drop_pct=track["drop_pct"])
    storage.save_deal(deal)
    summary.underwritten += 1
    if result.passed and eligible:
        summary.passed += 1
        if track["is_new"]:
            summary.new_passing += 1
    return deal


def resolve_sources(sources: list[str] | None, cfg: dict[str, Any]) -> list[str]:
    chosen = [s.strip().lower() for s in (sources or cfg.get("schedule", {}).get(
        "sources", ["portals", "rss", "propertydata"])) if s.strip()]
    bad = set(chosen) - set(ALL_SOURCES)
    if bad:
        raise ValueError(f"unknown source(s): {', '.join(sorted(bad))}")
    return ["sample"] if "sample" in chosen else chosen


def run_scan(
    outcodes: list[str] | None = None, *, sources: list[str] | None = None, offline: bool = False,
    dry_run: bool = False, use_claude: bool = True, min_roce: float | None = None,
    storage: Storage | None = None, include_rss: bool = True, config_path: str | None = None,
    progress: Progress | None = None, trigger: str = "manual",
) -> RunSummary:
    load_dotenv()
    cfg = load_config(config_path)
    progress = progress or _noop
    if offline:
        sources = ["sample"]
    srcs = resolve_sources(sources, cfg)
    if not include_rss and "rss" in srcs:
        srcs.remove("rss")
    targets = [o.upper() for o in (outcodes or all_outcodes(cfg))]
    unknown = set(targets) - set(all_outcodes(cfg))
    if unknown:
        log.warning("Outcodes outside the configured target list: %s", ", ".join(sorted(unknown)))
    storage = storage or Storage()
    summary = RunSummary(outcodes=targets, sources=srcs)
    min_roce = cfg["thresholds"].get("min_roce_pct", 0.0) if min_roce is None else min_roce
    sample = srcs == ["sample"]
    has_pd = bool(os.environ.get("PROPERTYDATA_API_KEY"))

    run_id = storage.start_run(trigger, srcs)
    storage.audit("run_start", None, {"outcodes": targets, "sources": srcs, "dry_run": dry_run})
    status = "ok"
    try:
        # Comps provider: PropertyData if subscribed, else local portal/Land Registry data.
        if sample:
            client: DataClient = FixtureClient()
            summary.comps_source = "sample data"
        elif has_pd:
            client = PropertyDataClient(base_url=cfg["ingestion"]["propertydata_base_url"],
                                        delay=cfg["ingestion"].get("request_delay_seconds", 0.4))
            summary.comps_source = "PropertyData"
        else:
            from .local_comps import LocalMarketClient

            client = LocalMarketClient(storage, cfg)
            summary.comps_source = "portal asking prices & rents + Land Registry"
        analyst = None if sample else make_analyst(cfg, use_claude)
        if not sample and analyst is None:
            summary.notes.append("Claude not configured — refurb tier from keyword heuristic")
        notifier = Notifier(dry_run=dry_run)

        with ExitStack() as stack:
            session = None
            listings: list[RawListing] = []
            if "portals" in srcs:
                from .portals.collector import PortalSession

                progress("collect", "Starting browser…")
                session = stack.enter_context(PortalSession(cfg, storage, progress))
                listings += session.collect(targets)
                summary.portals = session.status
            if sample or ("propertydata" in srcs and has_pd):
                for oc in targets:
                    progress("collect", f"PropertyData sourced lists: {oc}" if not sample
                             else f"Sample data: {oc}")
                    listings += fetch_sourced(client, oc, cfg)
            elif "propertydata" in srcs:
                summary.notes.append("PropertyData skipped — no PROPERTYDATA_API_KEY")
            if "rss" in srcs:
                progress("collect", "Auction RSS feeds")
                listings += fetch_rss(cfg, set(targets))

            # de-duplicate across sources
            uniq: dict[str, RawListing] = {}
            for lst in listings:
                uniq.setdefault(lst.listing_id, lst)
            listings = list(uniq.values())
            summary.fetched = len(listings)
            log.info("Fetched %d listings across %s", len(listings), ", ".join(targets))

            for i, listing in enumerate(listings, 1):
                progress("underwrite", f"Underwriting {i}/{len(listings)}: "
                         f"{listing.address or listing.listing_id}", done=i, total=len(listings))
                try:
                    deal = process_listing(
                        listing, client=client, cfg=cfg, storage=storage, analyst=analyst,
                        summary=summary, use_external_fallbacks=not sample,
                        detail_fetcher=session.fetch_detail if session else None)
                except Exception as exc:  # noqa: BLE001 — one bad listing must not abort the run
                    log.exception("Failed on %s", listing.listing_id)
                    summary.errors.append(f"{listing.listing_id}: {exc}")
                    storage.audit("error", listing.listing_id, str(exc))
                    continue
                if not deal or not (deal.result.passed and deal.result.eligible):
                    continue
                roce = deal.result.roce_pct
                if roce is not None and roce < min_roce:
                    continue
                if storage.should_alert(deal) and notifier.send(deal):  # dry-run prints the memo
                    storage.mark_alerted(listing.listing_id)
                    summary.alerted += 1
            if session:
                summary.portals = session.status
            if summary.waiting_for_comps:
                summary.notes.append(
                    f"{summary.waiting_for_comps} listing(s) skipped for now: not enough local price/rent "
                    "comps yet for that type & size (fills up as the daily runs collect more, or add a "
                    "PropertyData key)")
    except Exception as exc:
        status = "error"
        summary.errors.append(f"run failed: {exc}")
        log.exception("Run failed")
        raise
    finally:
        if status == "ok" and summary.errors:
            status = "partial"
        if any(p.get("state") == "blocked" for p in summary.portals.values()):
            status = "partial"
        storage.finish_run(run_id, status, summary.as_dict())
        storage.audit("run_end", None, summary.as_dict())
        progress("done", "Finished")
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="brrr-scout", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outcode", action="append", help="Outcode to scan (repeatable). Default: all")
    ap.add_argument("--sources", help=f"Comma list from {', '.join(ALL_SOURCES)}")
    ap.add_argument("--daily", action="store_true",
                    help="Morning routine: sources from config schedule.sources, alerts on")
    ap.add_argument("--sample", "--offline", dest="sample", action="store_true",
                    help="Use bundled sample data (no network)")
    ap.add_argument("--min-roce", type=float, default=None, help="Only alert deals with ROCE >= this %%")
    ap.add_argument("--dry-run", action="store_true", help="Print memos instead of sending alerts")
    ap.add_argument("--no-claude", action="store_true", help="Use keyword heuristic for refurb tier")
    ap.add_argument("--no-rss", action="store_true", help="Skip auction RSS feeds")
    ap.add_argument("--db", help="SQLite path (default data/brrr.db or $BRRR_DB_PATH)")
    ap.add_argument("--config", help="Config YAML (default config/postcodes.yaml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    from .logs import setup_logging

    setup_logging(verbose=args.verbose)
    summary = run_scan(
        args.outcode, sources=args.sources.split(",") if args.sources else None,
        offline=args.sample, dry_run=args.dry_run, use_claude=not args.no_claude,
        min_roce=args.min_roce, storage=Storage(args.db) if args.db else None,
        include_rss=not args.no_rss, config_path=args.config,
        trigger="schedule" if args.daily else "cli",
    )
    print(
        f"\nScanned {', '.join(summary.outcodes)} via {', '.join(summary.sources)} — fetched "
        f"{summary.fetched}, pre-screen rejected {summary.pre_screen_rejected}, underwritten "
        f"{summary.underwritten}, passed all gates {summary.passed} ({summary.new_passing} new), "
        f"alerts sent {summary.alerted}, Claude calls {summary.claude_calls}, "
        f"errors {len(summary.errors)}"
    )
    for name, st in summary.portals.items():
        print(f"  {name}: {st['state']} — {st['listings']} listings, {st['rentals']} rentals"
              f"{' — ' + st['message'] if st['message'] else ''}")
    for n in summary.notes[:10]:
        print(f"  note: {n}")


if __name__ == "__main__":
    main()


def rescore_all(storage: Storage, config_path: str | None = None) -> int:
    """Re-run the deterministic gates on every saved deal with the current config (no network).
    Used after thresholds/costs are changed in Settings."""
    cfg = load_config(config_path)
    n = 0
    for deal in storage.query_deals(include_ineligible=True, limit=1_000_000):
        lst, comps, refurb = deal.listing, deal.comps, deal.refurb
        ocfg = config_for_outcode(cfg, lst.outcode)
        result = underwrite(
            listing_id=lst.listing_id, outcode=lst.outcode, asking_price=lst.asking_price,
            conservative_arv=comps.conservative_arv, monthly_rent=comps.conservative_monthly_rent,
            floor_area_sqft=comps.floor_area_sqft, refurb_tier=refurb.tier,
            params=UnderwritingParams.from_config(ocfg), category=lst.category)
        eligible, reasons = check_eligibility(lst, ocfg)
        if refurb.is_excluded_type:
            eligible = False
            reasons.append(f"Claude: {refurb.exclusion_reason or 'excluded property type'}")
        result.eligible, result.eligibility_reasons = eligible, reasons
        storage.save_deal(deal.model_copy(update={"result": result}))
        n += 1
    storage.audit("rescore", None, {"deals": n})
    return n
