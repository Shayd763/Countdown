"""Market-data ingestion.

`load_ohlcv` pulls historical candles from any ccxt exchange and caches them
locally. `synthetic_ohlcv` generates a reproducible mean-reverting price series
so the backtester, strategies, and tests can run with zero network access.

All returned frames share the same schema:
    index : pandas.DatetimeIndex (UTC, tz-aware), one row per bar
    cols  : open, high, low, close, volume  (floats)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

_TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _cache_path(cache_dir: str, exchange: str, symbol: str, timeframe: str) -> str:
    safe = f"{exchange}_{symbol.replace('/', '-')}_{timeframe}.csv"
    return os.path.join(cache_dir, safe)


def load_ohlcv(
    exchange: str,
    symbol: str,
    timeframe: str = "1h",
    since: str | None = None,
    cache_dir: str = ".cache",
    use_cache: bool = True,
) -> pd.DataFrame:
    """Fetch OHLCV candles from a ccxt exchange, with a local CSV cache.

    Paginates through the exchange's per-call limit until it reaches "now".
    Requires network + the optional ``ccxt`` dependency. For offline work
    (tests, CI, quick experiments) use :func:`synthetic_ohlcv` instead.
    """
    path = _cache_path(cache_dir, exchange, symbol, timeframe)
    if use_cache and os.path.exists(path):
        return _read_cache(path)

    try:
        import ccxt  # imported lazily so the package works without it installed
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise ImportError(
            "ccxt is required for live data. Install it (`pip install ccxt`) "
            "or use synthetic_ohlcv() for offline runs."
        ) from exc

    client = getattr(ccxt, exchange)({"enableRateLimit": True})
    if timeframe not in _TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe {timeframe!r}")
    step = _TIMEFRAME_MS[timeframe]

    since_ms = client.parse8601(since) if since else client.milliseconds() - 500 * step
    now_ms = client.milliseconds()
    rows: list[list[float]] = []
    while since_ms < now_ms:
        batch = client.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since_ms = batch[-1][0] + step
        if len(batch) < 1000:
            break

    df = _rows_to_frame(rows)
    if use_cache:
        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(path)
    return df


def _rows_to_frame(rows: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", *OHLCV_COLUMNS])
    df = df.drop_duplicates(subset="ts").sort_values("ts")
    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    df.index.name = "timestamp"
    return df[OHLCV_COLUMNS].astype(float)


def _read_cache(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "timestamp"
    return df[OHLCV_COLUMNS].astype(float)


def load_kraken_csv(path: str) -> pd.DataFrame:
    """Load a Kraken downloadable OHLCVT history CSV into the standard schema.

    Kraken's free historical dumps (support.kraken.com -> "Downloadable
    historical OHLCVT Data") ship one headerless CSV per pair/interval, named
    like ``XBTGBP_1440.csv`` (1440 = daily, in minutes). Columns are:

        timestamp(unix seconds), open, high, low, close, volume, trades

    This is the deepest, rate-limit-free source for UK backtesting. Note Kraken
    uses ``XBT`` for Bitcoin, so BTC/GBP is the ``XBTGBP`` file.
    """
    cols = ["time", "open", "high", "low", "close", "volume", "trades"]

    # Kraken files are headerless, but be tolerant of an accidental header row.
    with open(path) as fh:
        first = fh.readline().split(",", 1)[0].strip()
    has_header = not first.replace(".", "", 1).isdigit()

    df = pd.read_csv(
        path,
        header=0 if has_header else None,
        names=None if has_header else cols,
    )
    if has_header:
        df.columns = [c.strip().lower() for c in df.columns]
        if "time" not in df.columns and "timestamp" in df.columns:
            df = df.rename(columns={"timestamp": "time"})

    df.index = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
    df.index.name = "timestamp"
    return df[OHLCV_COLUMNS].astype(float).sort_index()


def load_coinmetrics_csv(path: str, price_col: str = "PriceUSD") -> pd.DataFrame:
    """Load a Coin Metrics community-network CSV (e.g. ``csv/btc.csv``).

    Coin Metrics publishes reputable daily reference-rate data openly on GitHub
    (github.com/coinmetrics/data). It provides a single daily ``PriceUSD`` per
    asset, **not** OHLC — so we set open=high=low=close=PriceUSD. That is an
    honest approximation for *daily* strategies (decide on today's price, fill
    on the next day's), but it carries no intraday high/low and is quoted in
    USD, not GBP. Good enough to research whether an edge exists; final
    validation should use venue-native OHLC (e.g. Kraken XBTGBP) before capital.
    """
    df = pd.read_csv(path, usecols=["time", price_col])
    df = df.dropna(subset=[price_col])
    idx = pd.to_datetime(df["time"], utc=True)
    idx.name = "timestamp"
    price = df[price_col].astype(float).to_numpy()
    out = pd.DataFrame(
        {"open": price, "high": price, "low": price, "close": price, "volume": 0.0},
        index=idx,
    )
    return out.sort_index()


def synthetic_ohlcv(
    n: int = 2000,
    timeframe: str = "1h",
    start: str = "2023-01-01T00:00:00Z",
    seed: int = 7,
    mean: float = 100.0,
    reversion: float = 0.05,
    vol: float = 0.6,
) -> pd.DataFrame:
    """Generate a reproducible mean-reverting OHLCV series (no network needed).

    Close prices follow a discrete Ornstein-Uhlenbeck process, which by
    construction contains a mean-reversion edge — useful for exercising the
    pipeline end to end. It is a *test fixture*, not evidence a strategy works
    on real markets; real data is what validates or kills a strategy.
    """
    rng = np.random.default_rng(seed)
    step = _TIMEFRAME_MS[timeframe]
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)

    close = np.empty(n)
    close[0] = mean
    for i in range(1, n):
        drift = reversion * (mean - close[i - 1])
        close[i] = close[i - 1] + drift + rng.normal(0.0, vol)
    close = np.abs(close)

    # Build plausible OHLC around each close.
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    intrabar = np.abs(rng.normal(0.0, vol, size=n))
    high = np.maximum(open_, close) + intrabar
    low = np.minimum(open_, close) - intrabar
    volume = np.abs(rng.normal(1000.0, 200.0, size=n))

    index = pd.date_range(start=start_dt, periods=n, freq=pd.Timedelta(milliseconds=step))
    index.name = "timestamp"
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
