"""Orchestrator CLI.

    python -m src.main --offline --dry-run           # fixtures, no network, print memos
    python -m src.main --outcode ME4 --min-roce 10   # live run for one outcode
    python -m src.main                               # all target outcodes, send alerts
"""

from __future__ import annotations

import argparse
import logging
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


@dataclass
class RunSummary:
    outcodes: list[str]
    fetched: int = 0
    pre_screen_rejected: int = 0
    underwritten: int = 0
    passed: int = 0
    alerted: int = 0
    claude_calls: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


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
) -> Deal | None:
    ocfg = config_for_outcode(cfg, listing.outcode)
    track = storage.upsert_listing(listing)

    ok, reasons = check_eligibility(listing, ocfg, allow_unknown_lease=True)
    if not ok:
        summary.pre_screen_rejected += 1
        storage.audit("pre_screen_reject", listing.listing_id, {"reasons": reasons})
        return None

    comps = enrich(listing, client, ocfg, use_external_fallbacks=use_external_fallbacks)
    if comps.conservative_arv <= 0 or comps.conservative_monthly_rent <= 0:
        storage.audit("no_comps", listing.listing_id, {"sources": comps.sources})
        summary.errors.append(f"{listing.listing_id}: insufficient comps")
        return None

    params = UnderwritingParams.from_config(ocfg)
    common = dict(
        listing_id=listing.listing_id, outcode=listing.outcode, asking_price=listing.asking_price,
        conservative_arv=comps.conservative_arv, monthly_rent=comps.conservative_monthly_rent,
        floor_area_sqft=comps.floor_area_sqft, params=params, category=listing.category,
    )

    # Cheap deterministic pre-gate: if it fails even with the lightest refurb, don't spend
    # a Claude call on it — the heuristic is good enough for the record.
    best_case = underwrite(refurb_tier="light", **common)
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
    return deal


def run_scan(
    outcodes: list[str] | None = None, *, offline: bool = False, dry_run: bool = False,
    use_claude: bool = True, min_roce: float | None = None, storage: Storage | None = None,
    include_rss: bool = True, config_path: str | None = None,
) -> RunSummary:
    load_dotenv()
    cfg = load_config(config_path)
    targets = [o.upper() for o in (outcodes or all_outcodes(cfg))]
    unknown = set(targets) - set(all_outcodes(cfg))
    if unknown:
        log.warning("Outcodes outside the configured target list: %s", ", ".join(sorted(unknown)))
    storage = storage or Storage()
    summary = RunSummary(outcodes=targets)
    min_roce = cfg["thresholds"].get("min_roce_pct", 0.0) if min_roce is None else min_roce

    client: DataClient = (
        FixtureClient() if offline
        else PropertyDataClient(base_url=cfg["ingestion"]["propertydata_base_url"],
                                delay=cfg["ingestion"].get("request_delay_seconds", 0.4))
    )
    analyst = None if offline else make_analyst(cfg, use_claude)
    notifier = Notifier(dry_run=dry_run)
    storage.audit("run_start", None, {"outcodes": targets, "offline": offline, "dry_run": dry_run})

    listings: list[RawListing] = []
    for oc in targets:
        listings.extend(fetch_sourced(client, oc, cfg))
    if include_rss and not offline:
        listings.extend(fetch_rss(cfg, set(targets)))
    summary.fetched = len(listings)
    log.info("Fetched %d listings across %s", len(listings), ", ".join(targets))

    for listing in listings:
        try:
            deal = process_listing(listing, client=client, cfg=cfg, storage=storage,
                                   analyst=analyst, summary=summary,
                                   use_external_fallbacks=not offline)
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

    storage.audit("run_end", None, summary.as_dict())
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="brrr-scout", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outcode", action="append", help="Outcode to scan (repeatable). Default: all")
    ap.add_argument("--min-roce", type=float, default=None, help="Only alert deals with ROCE >= this %%")
    ap.add_argument("--dry-run", action="store_true", help="Print memos instead of sending alerts")
    ap.add_argument("--offline", action="store_true", help="Use tests/fixtures instead of live APIs")
    ap.add_argument("--no-claude", action="store_true", help="Use keyword heuristic for refurb tier")
    ap.add_argument("--no-rss", action="store_true", help="Skip auction RSS feeds")
    ap.add_argument("--db", help="SQLite path (default data/brrr.db or $BRRR_DB_PATH)")
    ap.add_argument("--config", help="Config YAML (default config/postcodes.yaml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    summary = run_scan(
        args.outcode, offline=args.offline, dry_run=args.dry_run, use_claude=not args.no_claude,
        min_roce=args.min_roce, storage=Storage(args.db) if args.db else None,
        include_rss=not args.no_rss, config_path=args.config,
    )
    print(
        f"\nScanned {', '.join(summary.outcodes)} — fetched {summary.fetched}, "
        f"pre-screen rejected {summary.pre_screen_rejected}, underwritten {summary.underwritten}, "
        f"passed all gates {summary.passed}, alerts sent {summary.alerted}, "
        f"Claude calls {summary.claude_calls}, errors {len(summary.errors)}"
    )


if __name__ == "__main__":
    main()
