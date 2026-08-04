#!/usr/bin/env python3
"""'What should I do right now?' — print the trend filter's current position.

A systematic strategy is only useful if you can act on today's signal. Given a
price history (most recent bar last), this prints whether the trend filter says
INVESTED or CASH right now, the current price vs its moving average, and how far
you are from a flip — so you know how close the next decision is.

    python scripts/signal_today.py --coinmetrics-csv btc.csv --slow 200
    python scripts/signal_today.py --price-csv myprices.csv --date-col Date --price-col Close --slow 10

It reads price only; it does not place orders. Execution stays in your hands.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

from quantbot.data import load_coinmetrics_csv, load_kraken_csv  # noqa: E402


def _load_price(args) -> pd.Series:
    if args.coinmetrics_csv:
        return load_coinmetrics_csv(args.coinmetrics_csv)["close"]
    if args.kraken_csv:
        return load_kraken_csv(args.kraken_csv)["close"]
    if args.price_csv:
        df = pd.read_csv(args.price_csv, parse_dates=[args.date_col]).set_index(args.date_col)
        return df[args.price_col].astype(float).sort_index()
    raise SystemExit("Provide --coinmetrics-csv, --kraken-csv, or --price-csv")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coinmetrics-csv")
    parser.add_argument("--kraken-csv")
    parser.add_argument("--price-csv")
    parser.add_argument("--date-col", default="Date")
    parser.add_argument("--price-col", default="Close")
    parser.add_argument("--slow", type=int, default=200,
                        help="MA length in bars (200 for daily, 10 for monthly)")
    parser.add_argument("--band", type=float, default=0.0, help="hysteresis band, e.g. 0.02")
    args = parser.parse_args()

    price = _load_price(args).dropna()
    if len(price) < args.slow:
        raise SystemExit(f"Need >= {args.slow} bars, have {len(price)}")

    ma = price.rolling(args.slow).mean()
    last_price = float(price.iloc[-1])
    last_ma = float(ma.iloc[-1])
    gap = last_price / last_ma - 1.0

    if gap > args.band:
        position = "INVESTED (long)"
    elif gap < -args.band:
        position = "CASH (out of the market)"
    else:
        position = "HOLD current position (inside band)"

    print(f"As of {price.index[-1].date()}:")
    print(f"  Price            : {last_price:,.2f}")
    print(f"  {args.slow}-bar MA      : {last_ma:,.2f}")
    print(f"  Price vs MA      : {gap:+.2%}")
    print(f"  ---> POSITION    : {position}")
    if args.band:
        print(f"  (band = +/-{args.band:.1%}; flips only outside it)")
    print("\nThis is a signal, not an order. You place the trade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
