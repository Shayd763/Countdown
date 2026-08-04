#!/usr/bin/env python3
"""Backtest delta-neutral funding-rate arbitrage.

With real funding data (export from your venue):
    python scripts/run_funding.py --funding-csv bybit_btc_funding.csv

Without it (illustrative model calibrated to how BTC funding behaves — positive
in bull markets, negative in bears — derived from real spot price regime):
    python scripts/run_funding.py --illustrative --coinmetrics-csv btc.csv

The illustrative run shows the strategy's *shape* (steady carry, high Sharpe,
drawdowns in bear-market negative-funding stretches). Numbers are a model, not a
promise — validate on real funding data before sizing anything.
"""
from __future__ import annotations
import argparse, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import numpy as np, pandas as pd
from quantbot.data import load_funding_csv, load_coinmetrics_csv
from quantbot.funding import run_funding_arb, funding_summary


def illustrative_funding(coinmetrics_csv: str) -> pd.Series:
    """Model an 8h funding series from real BTC price regime. Funding tends
    positive when price is trending up (longs pay to be long) and negative in
    downtrends. Base ~ +11%/yr, modulated by 30-day momentum, with noise."""
    px = load_coinmetrics_csv(coinmetrics_csv)["close"]
    px = px[px.index >= pd.Timestamp("2018-01-01", tz="UTC")]
    mom = px.pct_change(30).fillna(0.0)
    rng = np.random.default_rng(11)
    base = 0.0001            # per-8h neutral funding ~ +11%/yr
    # expand daily price to 3 funding intervals/day
    daily = base + 0.0012 * mom.clip(-0.6, 0.6)
    idx8 = pd.date_range(px.index[0], px.index[-1], freq="8h", tz="UTC")
    f = daily.reindex(idx8, method="ffill").fillna(base)
    f = f + rng.normal(0, 0.00025, len(f))          # per-interval noise
    return f.clip(-0.003, 0.004)                     # exchange-style clamps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--funding-csv")
    ap.add_argument("--coinmetrics-csv")
    ap.add_argument("--illustrative", action="store_true")
    ap.add_argument("--per-leg-bps", type=float, default=2.0, help="fee per leg (institutional/maker)")
    args = ap.parse_args()

    if args.funding_csv:
        funding = load_funding_csv(args.funding_csv)
        src = f"real: {os.path.basename(args.funding_csv)}"
    elif args.illustrative and args.coinmetrics_csv:
        funding = illustrative_funding(args.coinmetrics_csv)
        src = "ILLUSTRATIVE model (not real funding data)"
    else:
        ap.error("provide --funding-csv, or --illustrative with --coinmetrics-csv")

    ppy = 1095.0  # 8h intervals per year
    print(f"Funding-rate arbitrage · {src} · {len(funding)} intervals "
          f"({funding.index[0].date()} -> {funding.index[-1].date()})")
    print(f"  avg funding {funding.mean()*ppy:+.1%}/yr · positive {int((funding>0).mean()*100)}% of intervals\n")
    print(f"  {'Policy':14}{'CAGR':>8}{'Sharpe':>8}{'MaxDD':>8}{'Switches':>10}")
    for mode, kw in [("always", {}), ("conditional", {"entry": 0.0, "exit": -1e-4})]:
        res = run_funding_arb(funding, mode=mode, per_leg_bps=args.per_leg_bps,
                              periods_per_year=ppy, **kw)
        m = funding_summary(res)
        print(f"  {mode:14}{m['cagr']:>+7.1%}{m['sharpe']:>8.2f}{m['max_drawdown']:>8.1%}{m['switches']:>10}")
    print("\n  Delta-neutral: BTC price P&L cancels; return is funding minus costs.")
    print("  Main risk: extended negative funding in bear markets + liquidation/basis risk on the short leg.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
