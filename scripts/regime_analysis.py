#!/usr/bin/env python3
"""Regime stress test for the trend-filter strategy.

A full-history Sharpe number can hide that a strategy only works in one market
regime. This breaks BTC's history into labelled bull / bear / chop periods and
measures the long/flat trend filter (vs buy & hold) in each — the honest way to
see where the edge really comes from and where the strategy suffers.

    python scripts/regime_analysis.py --coinmetrics-csv path/to/btc.csv
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

from quantbot.backtest import performance_summary, run_backtest  # noqa: E402
from quantbot.data import load_coinmetrics_csv  # noqa: E402
from quantbot.strategies import TrendFollowing  # noqa: E402

# Approximate BTC regimes (UTC). Bear = deep drawdown, chop = sideways/recovery.
REGIMES = [
    ("2018 BEAR",         "2018-01-01", "2018-12-15"),
    ("2019 CHOP",         "2019-01-01", "2020-02-15"),
    ("2020-21 BULL",      "2020-03-15", "2021-11-10"),
    ("2022 BEAR",         "2021-11-10", "2022-12-31"),
    ("2023 CHOP",         "2023-01-01", "2023-12-31"),
    ("2024-26 BULL",      "2024-01-01", "2026-05-23"),
]


def _stats(sub: pd.DataFrame, signal: pd.Series, fee_bps: float, slippage_bps: float) -> dict:
    r = run_backtest(sub, signal, fee_bps=fee_bps, slippage_bps=slippage_bps)
    m = performance_summary(r)
    m["exposure"] = float((r.positions != 0).mean())
    return m


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coinmetrics-csv", required=True)
    parser.add_argument("--slow", type=int, default=200, help="slow MA length")
    parser.add_argument("--band", type=float, default=0.05, help="whipsaw guard band")
    parser.add_argument("--fee-bps", type=float, default=26)
    parser.add_argument("--slippage-bps", type=float, default=5)
    args = parser.parse_args()

    full = load_coinmetrics_csv(args.coinmetrics_csv)
    # Signals on full history so the MA is warmed before each sliced regime.
    plain = TrendFollowing(1, args.slow, band=0.0, allow_short=False).generate_signals(full)
    banded = TrendFollowing(1, args.slow, band=args.band, allow_short=False).generate_signals(full)

    print(f"Trend filter (close vs {args.slow}d MA), long/flat, after "
          f"{args.fee_bps:.0f}bps fees. Band guard = {args.band:.0%}\n")
    hdr = f"{'Regime':14}{'B&H':>8}{'Filter':>8}{'F-DD':>7}{'Banded':>8}{'B-DD':>7}{'%inMkt':>8}"
    print(hdr)
    print("-" * len(hdr))
    for label, a, b in REGIMES:
        mask = (full.index >= pd.Timestamp(a, tz="UTC")) & (full.index <= pd.Timestamp(b, tz="UTC"))
        sub = full[mask]
        bh = _stats(sub, pd.Series(1.0, index=sub.index), args.fee_bps, args.slippage_bps)
        f = _stats(sub, plain.loc[sub.index], args.fee_bps, args.slippage_bps)
        bd = _stats(sub, banded.loc[sub.index], args.fee_bps, args.slippage_bps)
        print(f"{label:14}{bh['total_return']:>+7.0%}{f['total_return']:>+8.0%}"
              f"{f['max_drawdown']:>+7.0%}{bd['total_return']:>+8.0%}"
              f"{bd['max_drawdown']:>+7.0%}{bd['exposure']:>8.0%}")

    print("\nReading it: the filter's edge is DRAWDOWN REDUCTION in bears, paid for by")
    print("giving up upside in bulls/chop. It is a risk overlay, not a money machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
