#!/usr/bin/env python3
"""Full walk-forward validation of the multi-factor ensemble.

The ensemble has no per-fold *fitted* weights, but the factor set and trend
length were chosen while looking at all of history. This test removes that
hindsight: on each training window it re-selects the factor combination and
trend length by training Sharpe, then scores that choice on the *next* unseen
window. It answers "would the whole selection process have worked in real time?"

Result (real Coin Metrics BTC, 2016-2026, 2yr train / 6mo test, Kraken fees +
4.5% cash yield):
    ensemble   OOS Sharpe ~0.97,  CAGR ~+38%,  MaxDD ~-57%
    buy & hold OOS Sharpe ~0.69,               MaxDD  ~-84%

A real edge over buy & hold on risk-adjusted terms AND drawdown — BUT with
huge fold-to-fold variance: roughly half of the 6-month windows are negative,
and the aggregate is carried by a few strong folds. This is a volatile,
regime-dependent edge that demands stomach for long underperformance, not a
smooth outperformer. Size and expectations accordingly.

    python scripts/ensemble_walkforward.py --coinmetrics-csv path/to/btc.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quantbot.backtest import run_backtest  # noqa: E402
from quantbot.data import load_coinmetrics_csv  # noqa: E402
from quantbot.strategies import build_strategy  # noqa: E402

FACTOR_SETS = [("trend", "flow", "fee"), ("trend", "flow"), ("trend", "fee"),
               ("trend", "flow", "fee", "addr")]
TREND_MAS = [150, 200, 250]


def _sharpe(r: pd.Series) -> float:
    r = r.fillna(0.0)
    return r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coinmetrics-csv", required=True)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--train", type=int, default=730)
    ap.add_argument("--test", type=int, default=182)
    args = ap.parse_args()

    df = load_coinmetrics_csv(args.coinmetrics_csv, with_onchain=True)
    df = df[df.index >= pd.Timestamp(args.start, tz="UTC")]

    # Precompute each candidate spec's signal on full history (proper warmup).
    specs = [(fs, tm) for fs in FACTOR_SETS for tm in TREND_MAS]
    sigs = {s: build_strategy("ensemble", factors=s[0], trend_ma=s[1]).generate_signals(df)
            for s in specs}
    bt = dict(fee_bps=26, slippage_bps=5, annual_cash_yield=0.045)

    n, start, oos, picks, fold_sh = len(df), 0, [], [], []
    while start + args.train + args.test <= n:
        tr = slice(start, start + args.train)
        te = slice(start + args.train, start + args.train + args.test)
        best = max(specs, key=lambda s: _sharpe(run_backtest(df.iloc[tr], sigs[s].iloc[tr], **bt).returns))
        r_te = run_backtest(df.iloc[te], sigs[best].iloc[te], **bt).returns
        oos.append(r_te)
        picks.append(best)
        fold_sh.append(_sharpe(r_te))
        start += args.test

    oos = pd.concat(oos)
    oos = oos[~oos.index.duplicated()]
    eq = (1 + oos.fillna(0)).cumprod()
    bh = df.loc[oos.index]
    bhr = run_backtest(bh, pd.Series(1.0, index=bh.index), fee_bps=26, slippage_bps=5).returns

    print(f"Walk-forward: {len(picks)} folds, {oos.index[0].date()} -> {oos.index[-1].date()}")
    print(f"  OOS Sharpe  ensemble : {_sharpe(oos):.2f}")
    print(f"  OOS Sharpe  buy&hold : {_sharpe(bhr):.2f}")
    print(f"  OOS CAGR    ensemble : {eq.iloc[-1] ** (365.25 / len(eq)) - 1:+.1%}")
    print(f"  OOS MaxDD   ensemble : {(eq / eq.cummax() - 1).min():.0%}")
    pos = sum(1 for x in fold_sh if x > 0)
    print(f"  positive folds       : {pos}/{len(fold_sh)}  (edge is real but volatile)")
    print(f"  factor-set picks     : {dict(Counter('+'.join(fs) for fs, _ in picks))}")
    print(f"  trend_ma picks       : {dict(Counter(tm for _, tm in picks))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
