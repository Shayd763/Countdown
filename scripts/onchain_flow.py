#!/usr/bin/env python3
"""On-chain exchange-flow signal for BTC — walk-forward validated.

Thesis: coins moving ONTO exchanges tend to precede selling; coins moving OFF
(into self-custody) tend to precede holding/accumulation. So go long when net
exchange flow is unusually negative (outflows) and step to cash when it turns
unusually positive (inflows). "Unusual" = rolling z-score of net flow, so there
are no hindsight thresholds. This uses data a price-only algo can't see.

Result (real Coin Metrics BTC data, 2016-2026, walk-forward: thresholds chosen
per 2yr train window, scored on the next 6mo out-of-sample, Kraken fees + 4.5%
cash yield):
    flow signal   OOS Sharpe 0.76,  MaxDD -58%
    buy & hold    OOS Sharpe 0.69,  MaxDD -84%

A MARGINAL edge — the first signal in this project to survive walk-forward at
all — driven mostly by drawdown reduction, with stable parameter picks across
folds. Promising but not decisive; treat as a lead to harden, not a proven
money-maker. Caveat: exchange-flow labeling is estimated and imperfect.

Other on-chain signals tested and REJECTED (badly underperformed buy & hold OOS):
MVRV valuation z-score, cross-sectional momentum.

    python scripts/onchain_flow.py --coinmetrics-csv path/to/btc.csv
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quantbot.backtest import run_backtest  # noqa: E402


def _sharpe(r: pd.Series) -> float:
    r = r.fillna(0.0)
    return r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0.0


def _signal(z: pd.Series, long_below: float, exit_above: float) -> pd.Series:
    pos, out = 0.0, np.zeros(len(z))
    for i, x in enumerate(z.to_numpy()):
        if np.isnan(x):
            out[i] = 0.0
            continue
        if pos == 0.0 and x < long_below:
            pos = 1.0
        elif pos == 1.0 and x > exit_above:
            pos = 0.0
        out[i] = pos
    return pd.Series(out, index=z.index)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coinmetrics-csv", required=True, help="Coin Metrics btc.csv with flow cols")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--train", type=int, default=730)
    ap.add_argument("--test", type=int, default=182)
    args = ap.parse_args()

    d = pd.read_csv(args.coinmetrics_csv,
                    usecols=["time", "PriceUSD", "FlowInExNtv", "FlowOutExNtv"],
                    parse_dates=["time"]).dropna().set_index("time")
    d.index = d.index.tz_localize("UTC")
    d = d[d.index >= pd.Timestamp(args.start, tz="UTC")]
    v = d["PriceUSD"].to_numpy()
    df = pd.DataFrame({"open": v, "high": v, "low": v, "close": v, "volume": 0.0}, index=d.index)
    netflow = d["FlowInExNtv"] - d["FlowOutExNtv"]

    zs = {w: (netflow - netflow.rolling(w).mean()) / netflow.rolling(w).std() for w in (60, 90, 120)}
    grid = list(itertools.product((60, 90, 120), (-0.5, -1.0, -1.5), (0.5, 1.0, 1.5)))
    bt = dict(fee_bps=26, slippage_bps=5, annual_cash_yield=0.045)

    dates = df.index
    oos, picks, start = [], [], 0
    while start + args.train + args.test <= len(dates):
        tr = slice(start, start + args.train)
        te = slice(start + args.train, start + args.train + args.test)
        best = None
        for (w, lb, ea) in grid:
            r = run_backtest(df.iloc[tr], _signal(zs[w], lb, ea).iloc[tr], **bt).returns
            sc = _sharpe(r)
            if best is None or sc > best[0]:
                best = (sc, (w, lb, ea))
        w, lb, ea = best[1]
        oos.append(run_backtest(df.iloc[te], _signal(zs[w], lb, ea).iloc[te], **bt).returns)
        picks.append((w, lb, ea))
        start += args.test

    oos = pd.concat(oos)
    oos = oos[~oos.index.duplicated()]
    eq = (1 + oos.fillna(0)).cumprod()
    bh = df.loc[oos.index]
    bhr = run_backtest(bh, pd.Series(1.0, index=bh.index), fee_bps=26, slippage_bps=5).returns

    print(f"Walk-forward OOS over {len(picks)} folds ({oos.index[0].date()} -> {oos.index[-1].date()}):")
    print(f"  Flow signal : Sharpe {_sharpe(oos):.2f}  CAGR {eq.iloc[-1]**(365.25/len(eq))-1:+.1%}"
          f"  MaxDD {(eq/eq.cummax()-1).min():.0%}")
    print(f"  Buy & hold  : Sharpe {_sharpe(bhr):.2f}")
    print("  A marginal edge (mostly drawdown reduction); harden before trusting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
