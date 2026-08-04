"""Live data feeds — fetch fresh price + on-chain data and merge to the schema
the signal core needs: [open, high, low, close, volume, flow_in, flow_out, fee_ntv].

Network-dependent (won't run in a no-egress sandbox); designed to run on your own
machine or server. Price comes from Kraken's public OHLC endpoint (no API key
needed); on-chain factors from Coin Metrics' open community data on GitHub.

Both sources are read-only and keyless — only *trading* needs your Kraken keys.
"""

from __future__ import annotations

import io
import urllib.request

import pandas as pd

KRAKEN_OHLC = "https://api.kraken.com/0/public/OHLC"
COINMETRICS_BTC = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv"


def _get(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (trusted hosts)
        return r.read()


def fetch_kraken_ohlc(pair: str = "XXBTZUSD", interval: int = 1440) -> pd.DataFrame:
    """Daily OHLC from Kraken's public API (last ~720 bars; no key needed).

    For deep history combine this with the downloadable Kraken CSV via
    ``quantbot.data.load_kraken_csv`` — the live loop only needs recent bars to
    extend that history.
    """
    import json

    raw = json.loads(_get(f"{KRAKEN_OHLC}?pair={pair}&interval={interval}"))
    if raw.get("error"):
        raise RuntimeError(f"Kraken API error: {raw['error']}")
    key = next(k for k in raw["result"] if k != "last")
    rows = raw["result"][key]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vwap", "volume", "count"])
    df.index = pd.to_datetime(df["ts"], unit="s", utc=True)
    df.index.name = "timestamp"
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def fetch_coinmetrics_onchain() -> pd.DataFrame:
    """Daily BTC on-chain factors (flow_in, flow_out, fee_ntv) from Coin Metrics."""
    raw = _get(COINMETRICS_BTC)
    cols = ["time", "FlowInExNtv", "FlowOutExNtv", "FeeTotNtv"]
    df = pd.read_csv(io.BytesIO(raw), usecols=cols).dropna()
    df.index = pd.to_datetime(df["time"], utc=True)
    df.index.name = "timestamp"
    return df.rename(columns={"FlowInExNtv": "flow_in", "FlowOutExNtv": "flow_out",
                              "FeeTotNtv": "fee_ntv"})[["flow_in", "flow_out", "fee_ntv"]].astype(float)


def build_dataset(history: pd.DataFrame | None = None, pair: str = "XXBTZUSD") -> pd.DataFrame:
    """Assemble the live dataset: fresh Kraken prices + Coin Metrics factors,
    optionally extending a deep-history frame (from the downloadable Kraken CSV).

    Returns a daily frame indexed by UTC date with the columns the signal needs.
    """
    price = fetch_kraken_ohlc(pair)
    if history is not None:
        price = pd.concat([history[["open", "high", "low", "close", "volume"]], price])
        price = price[~price.index.duplicated(keep="last")].sort_index()
    onchain = fetch_coinmetrics_onchain()
    merged = price.join(onchain, how="left")
    # Carry the last known on-chain values forward if today's aren't published yet.
    merged[["flow_in", "flow_out", "fee_ntv"]] = merged[["flow_in", "flow_out", "fee_ntv"]].ffill()
    return merged.dropna(subset=["close"])
