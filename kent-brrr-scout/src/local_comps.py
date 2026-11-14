"""Free comps provider for when there is no PropertyData subscription.

Answers the same endpoint shapes `enrichment.py` asks PropertyData for, but from:
- asking prices & rents of everything the portal collector has seen (`market` table)
- HM Land Registry Price Paid (SPARQL), cached, scaled to the bedroom count using the
  local asking-price ratio (Land Registry records no bedrooms).
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from .enrichment import land_registry_sold
from .http import PropertyDataError
from .storage import Storage

FROM_PD_TYPE = {
    "flat": "flat", "terraced_house": "terraced", "semi-detached_house": "semi-detached",
    "detached_house": "detached",
}


# house type -> (fallback type, price ratio this/fallback)
HOUSE_FALLBACK = {"semi-detached": ("terraced", 1.12), "terraced": ("semi-detached", 1 / 1.12),
                  "end-terrace": ("semi-detached", 1 / 1.08)}


class LocalMarketClient:
    provides_land_registry = True

    def __init__(self, storage: Storage, cfg: dict[str, Any]):
        self.storage = storage
        self.cfg = cfg
        lc = cfg.get("local_comps", {})
        self.lookback = int(lc.get("lookback_days", 120))
        self.min_n = int(lc.get("min_samples", 3))
        self.lr_cache_days = int(lc.get("land_registry_cache_days", 7))

    def _since(self) -> str:
        return (datetime.now(UTC) - timedelta(days=self.lookback)).isoformat(timespec="seconds")

    def _median(self, oc: str, channel: str, ptype: str | None, beds: Any) -> tuple[float | None, int]:
        beds = int(beds) if beds not in (None, "", "None") else None
        prices = self.storage.market_prices(oc, channel, ptype, beds, self._since())
        if len(prices) >= self.min_n:
            return statistics.median(prices), len(prices)
        return None, len(prices)

    def get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        ep = endpoint.strip("/")
        oc = str(params.get("postcode", "")).split(" ")[0].upper()
        ptype = FROM_PD_TYPE.get(str(params.get("type")))
        beds = params.get("bedrooms")

        if ep == "prices":
            med, n = self._median(oc, "sale", ptype, beds)
            if med is None and ptype in HOUSE_FALLBACK:
                # Too few of this house type with this bed count: borrow the neighbouring
                # house type, adjusted by a typical price ratio.
                other, factor = HOUSE_FALLBACK[ptype]
                alt, n2 = self._median(oc, "sale", other, beds)
                if alt is not None:
                    med, n = alt * factor, n2
            if med is None:
                raise PropertyDataError(f"only {n} local asking prices for {oc} {ptype} {beds}b")
            return {"status": "success", "data": {"average": med, "points_analysed": n,
                                                  "source": "portal_asking"}}

        if ep == "rents":
            med, n = self._median(oc, "rent", ptype, beds)
            if med is None:  # fall back to any type with the same bedroom count
                med, n = self._median(oc, "rent", None, beds)
            if med is None:
                raise PropertyDataError(f"only {n} local rents for {oc} {beds}b")
            return {"status": "success", "data": {"long_let": {
                "units": "gbp_per_month", "average": med, "points_analysed": n}}}

        if ep == "sold-prices":
            prices = self._land_registry(oc, ptype or "")
            if not prices:
                raise PropertyDataError(f"no Land Registry sales for {oc} {ptype}")
            ratio = 1.0
            if beds not in (None, ""):
                with_beds, _ = self._median(oc, "sale", ptype, beds)
                all_beds, _ = self._median(oc, "sale", ptype, None)
                if with_beds and all_beds:
                    ratio = with_beds / all_beds
            return {"status": "success", "data": {"raw_data": [
                {"price": round(p * ratio, -2), "type": params.get("type")} for p in prices]}}

        raise PropertyDataError(f"{ep} not available from local comps")

    def _land_registry(self, outcode: str, ptype: str) -> list[float]:
        key = f"lr_sold:{outcode}:{ptype}"
        cached = self.storage.kv_get(key)
        if cached and datetime.fromisoformat(cached["at"]) > datetime.now(UTC) - timedelta(
                days=self.lr_cache_days):
            return cached["prices"]
        prices = land_registry_sold(outcode, ptype, self.cfg["ingestion"]["land_registry_sparql"])
        if prices:
            self.storage.kv_set(key, {"at": datetime.now(UTC).isoformat(), "prices": prices})
        return prices or (cached or {}).get("prices", [])
