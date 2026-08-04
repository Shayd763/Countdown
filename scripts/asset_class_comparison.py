#!/usr/bin/env python3
"""Cross-asset evaluation: which asset class deserves a systematic bot?

Runs the identical trend-filter-vs-buy-&-hold test (Faber-style 10-month SMA,
long/flat) across equities, gold, FX, and crypto on a common monthly basis.
The goal is to see, with real data, where returns and edge actually live.

Data sources (all reachable via GitHub raw; download into --data-dir):
  sp500.csv    datasets/s-and-p-500        (Shiller monthly, 'SP500' price col)
  gold.csv     datasets/gold-prices        ('Price' col)
  fx_daily.csv datasets/exchange-rates     (Fed H.10 daily, long format)
  btc_cm.csv   coinmetrics/data csv/btc.csv ('PriceUSD' col)

Key finding this exposes: the dominant driver of returns is the asset's own
risk premium (crypto, equities have one; FX has ~none), not strategy cleverness.
Trend-following is a risk overlay that shines on assets with a premium AND deep
recoverable drawdowns — it cannot manufacture return where there is no drift.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

from quantbot.backtest import performance_summary, run_backtest  # noqa: E402
from quantbot.strategies import TrendFollowing  # noqa: E402


def _frame(price: pd.Series) -> pd.DataFrame:
    price = price.dropna().astype(float)
    idx = price.index if price.index.tz else pd.DatetimeIndex(price.index).tz_localize("UTC")
    v = price.values
    return pd.DataFrame({"open": v, "high": v, "low": v, "close": v, "volume": 0.0}, index=idx)


def _load(data_dir: str) -> dict[str, pd.Series]:
    p = lambda f: os.path.join(data_dir, f)  # noqa: E731
    sp = pd.read_csv(p("sp500.csv"), parse_dates=["Date"]).set_index("Date")["SP500"].resample("ME").last()
    gd = pd.read_csv(p("gold.csv"), parse_dates=["Date"]).set_index("Date")["Price"].resample("ME").last()
    fx = pd.read_csv(p("fx_daily.csv"), parse_dates=["Date"])
    gbp = (fx[fx["Country"].str.contains("Kingdom", case=False, na=False)]
           .set_index("Date")["Exchange rate"].astype(float).resample("ME").last())
    btc = (pd.read_csv(p("btc_cm.csv"), parse_dates=["time"], usecols=["time", "PriceUSD"])
           .dropna().set_index("time")["PriceUSD"].resample("ME").last())
    return {"Equities (S&P)": sp, "Gold": gd, "FX (GBP/USD)": gbp, "Crypto (BTC)": btc}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="dir holding the four CSVs")
    parser.add_argument("--start", default=None, help="ISO date, e.g. 2015-01-01 (else full history)")
    parser.add_argument("--slow", type=int, default=10, help="trend MA length in months")
    parser.add_argument("--cost-bps", type=float, default=10)
    args = parser.parse_args()

    assets = _load(args.data_dir)
    start = pd.Timestamp(args.start, tz="UTC") if args.start else None

    print(f"Trend filter ({args.slow}-month SMA, long/flat) vs Buy & Hold, "
          f"~{args.cost_bps:.0f}bps/trade" + (f", from {args.start}" if start else ", full history"))
    print(f"{'Asset':16}{'B&H CAGR':>9}{'B&H Shp':>8}{'B&H DD':>8}  | "
          f"{'TF CAGR':>8}{'TF Shp':>7}{'TF DD':>7}{'Trades':>7}  Trend helps?")
    for name, price in assets.items():
        df = _frame(price)
        if start is not None:
            df = df[df.index >= start]
        bh = performance_summary(run_backtest(df, pd.Series(1.0, index=df.index),
                                              fee_bps=args.cost_bps, slippage_bps=0))
        sig = TrendFollowing(1, args.slow, 0.0, allow_short=False).generate_signals(df)
        tf = performance_summary(run_backtest(df, sig, fee_bps=args.cost_bps, slippage_bps=0))
        if tf["sharpe"] > bh["sharpe"] and tf["max_drawdown"] > bh["max_drawdown"]:
            helps = "YES"
        elif tf["max_drawdown"] > bh["max_drawdown"]:
            helps = "risk only"
        else:
            helps = "no"
        print(f"{name:16}{bh['cagr']:>+8.1%}{bh['sharpe']:>8.2f}{bh['max_drawdown']:>8.0%}  | "
              f"{tf['cagr']:>+8.1%}{tf['sharpe']:>7.2f}{tf['max_drawdown']:>7.0%}{tf['n_trades']:>7}   {helps}")

    print("\nReturns track the asset's risk premium first, strategy second. FX has ~no")
    print("premium (zero-sum relative price) -> worst base for a directional bot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
