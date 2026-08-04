#!/usr/bin/env python3
"""Cross-sectional crypto momentum — research + honest verdict.

Tests whether rotating into the strongest-momentum coins (rank a basket by
trailing return, hold the top K equally, rebalance weekly, optional BTC-200d
market-regime filter) produces edge over just holding BTC or an equal-weight
basket. Costs and turnover are included.

VERDICT (real Coin Metrics data, 12 majors, 2018-2026): NO durable edge.
Full-period numbers look good, but they are driven by (a) survivorship bias —
only coins that survived are in the data, so momentum never gets to chase the
ones that went to zero — and (b) the 2018-2022 half. Out-of-sample (2022-2026)
the best config's Sharpe collapses from 1.33 to 0.43, worse than holding BTC.
Kept as a documented negative result: this is the OOS test doing its job.

    python scripts/crypto_momentum.py --coins-dir path/to/coins   # dir of <ticker>.csv
"""

from __future__ import annotations

import argparse
import glob
import os

import numpy as np
import pandas as pd


def load_panel(coins_dir: str, start: str = "2018-01-01", min_rows: int = 400) -> pd.DataFrame:
    series = {}
    for f in glob.glob(os.path.join(coins_dir, "*.csv")):
        ticker = os.path.basename(f)[:-4]
        if "PriceUSD" not in pd.read_csv(f, nrows=0).columns:
            continue
        s = pd.read_csv(f, usecols=["time", "PriceUSD"], parse_dates=["time"]).dropna()
        if len(s) >= min_rows:
            series[ticker] = s.set_index("time")["PriceUSD"].astype(float)
    panel = pd.DataFrame(series).sort_index().resample("D").last()
    return panel[panel.index >= pd.Timestamp(start)]


def sharpe(ret: pd.Series) -> float:
    ret = ret.fillna(0.0)
    return ret.mean() / ret.std() * np.sqrt(365) if ret.std() > 0 else 0.0


def momentum_returns(panel, daily_ret, risk_on, lookback, top_k, rebal=7, cost_bps=31, regime=False):
    dates = panel.index
    target = pd.DataFrame(0.0, index=dates, columns=panel.columns)
    last_w = pd.Series(0.0, index=panel.columns)
    for i in range(len(dates)):
        if i >= lookback and (i - lookback) % rebal == 0:
            if regime and not bool(risk_on.iloc[i]):
                last_w = pd.Series(0.0, index=panel.columns)
            else:
                mom = (panel.iloc[i] / panel.iloc[i - lookback] - 1.0).dropna()
                sel = mom[mom > 0].sort_values(ascending=False).head(top_k)
                last_w = pd.Series(0.0, index=panel.columns)
                if len(sel):
                    last_w[sel.index] = 1.0 / len(sel)
        target.iloc[i] = last_w
    port = (target.shift(1) * daily_ret).sum(axis=1)
    turnover = (target - target.shift(1)).abs().sum(axis=1)
    return port - turnover * cost_bps / 10000.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coins-dir", required=True, help="dir of Coin Metrics <ticker>.csv files")
    ap.add_argument("--split", default="2022-06-01", help="in/out-of-sample split date")
    args = ap.parse_args()

    panel = load_panel(args.coins_dir)
    daily_ret = panel.pct_change()
    risk_on = panel["btc"] > panel["btc"].rolling(200).mean()
    split = pd.Timestamp(args.split)

    def halves(ret):
        return sharpe(ret[ret.index < split]), sharpe(ret[ret.index >= split])

    print(f"Universe: {list(panel.columns)}  ({panel.index[0].date()} -> {panel.index[-1].date()})")
    print(f"\n{'Strategy':28}{'in-sample Shp':>15}{'OUT-OF-SAMPLE Shp':>19}")
    a, b = halves(daily_ret["btc"]);            print(f"{'BTC buy & hold':28}{a:>15.2f}{b:>19.2f}")
    a, b = halves(daily_ret.mean(axis=1));      print(f"{'Equal-weight basket':28}{a:>15.2f}{b:>19.2f}")
    for lb, k in [(30, 5), (60, 3)]:
        r = momentum_returns(panel, daily_ret, risk_on, lb, k, regime=True)
        a, b = halves(r)
        print(f"{'Mom regime lb=%d top=%d' % (lb, k):28}{a:>15.2f}{b:>19.2f}")
    print("\nOut-of-sample Sharpe near or below buy & hold => no durable edge (rejected).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
