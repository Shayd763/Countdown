"""Per-run deal funnel: how many listings reach each stage, where they drop out and why.

Stages, in order:
  seen        listings collected this run (after de-duplication and locating)
  unchanged   seen again at the same price and checked recently, so skipped
  excluded    failed the property filters (type, lease, keywords, category)
  no_comps    not enough price or rent evidence yet for that district
  checked     fully underwritten
  possible    would pass with a light refurb (gets a detail page and a Claude read)
  passed      passes all four gates
  worth_offer fails at asking but passes at the highest offer that clears the cap
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

STAGES = ["seen", "unchanged", "excluded", "no_comps", "checked", "possible", "passed", "worth_offer"]


def reason_bucket(reason: str) -> str:
    r = reason.lower()
    if "keyword" in r:
        word = reason.split("'")[1] if "'" in reason else "keyword"
        return f"Excluded wording: {word}"
    if "lease" in r:
        return "Lease too short or unknown"
    if "property type" in r:
        kind = reason.split("'")[1] if "'" in reason else "other"
        return f"Property type: {kind}"
    if "excluded by config" in r:
        return "Commercial switched off"
    if "leasehold house" in r:
        return "Leasehold house"
    if r.startswith("claude"):
        return "Claude: excluded type"
    return reason[:60]


class Funnel:
    def __init__(self) -> None:
        self.total: Counter = Counter()
        self.by_region: dict[str, Counter] = defaultdict(Counter)
        self.by_search: dict[str, Counter] = defaultdict(Counter)
        self.reasons: Counter = Counter()

    def add(self, stage: str, listing: Any, reason: str | None = None) -> None:
        region = getattr(listing, "region", "") or "Unknown"
        kind = getattr(listing, "search_kind", "") or "new"
        for c in (self.total, self.by_region[region], self.by_search[kind]):
            c[stage] += 1
        if reason:
            self.reasons[reason_bucket(reason)] += 1

    def gate_failures(self, gates: list[Any]) -> None:
        for g in gates:
            if not g.passed:
                self.reasons[{"all_in_cap": "Fails: all-in over 75% of value",
                              "gross_yield": "Fails: gross yield too low",
                              "net_cashflow": "Fails: cashflow after tax",
                              "capital_recycled": "Fails: too little cash back"}.get(g.name, g.name)] += 1

    def as_dict(self) -> dict[str, Any]:
        order = lambda c: {s: c.get(s, 0) for s in STAGES}  # noqa: E731
        return {
            "stages": STAGES,
            "total": order(self.total),
            "by_region": {k: order(v) for k, v in sorted(self.by_region.items(), key=lambda kv: -kv[1]["seen"])},
            "by_search": {k: order(v) for k, v in self.by_search.items()},
            "reasons": dict(self.reasons.most_common(15)),
        }
