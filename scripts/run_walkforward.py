#!/usr/bin/env python3
"""Run walk-forward out-of-sample validation.

This is the honest test: parameters are chosen on each train window and scored
on the next unseen test window, with realistic costs. If the stitched
out-of-sample curve isn't profitable after fees, the strategy is rejected.

    python scripts/run_walkforward.py --synthetic
    python scripts/run_walkforward.py --synthetic --strategy trend
    python scripts/run_walkforward.py --kraken-csv XBTGBP_1440.csv --strategy trend
    python scripts/run_walkforward.py --config config/config.yaml
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

from quantbot.data import (  # noqa: E402
    load_coinmetrics_csv,
    load_kraken_csv,
    load_ohlcv,
    synthetic_ohlcv,
)
from quantbot.validation import walk_forward  # noqa: E402

# Parameter grids searched within each training window, per strategy.
PARAM_GRIDS = {
    "mean_reversion": {
        "lookback": [20, 40, 60],
        "entry_z": [1.5, 2.0, 2.5],
        "exit_z": [0.25, 0.5],
        "allow_short": [False],   # UK spot-only
        "cost_bps": [52],        # round-trip cost gate (2 x 26 bps taker)
        "edge_safety": [1.5],    # require expected move >= 1.5x round-trip cost
    },
    "trend": {
        "fast": [10, 20, 50],
        "slow": [50, 100, 200],
        "band": [0.0, 0.02],     # hysteresis to cut whipsaw / saved round trips
        "allow_short": [False],   # UK spot-only
    },
}

# Compact per-fold parameter display, per strategy.
_FMT = {
    "mean_reversion": lambda p: f"lb={p.get('lookback')} ez={p.get('entry_z')} xz={p.get('exit_z')}",
    "trend": lambda p: f"fast={p.get('fast')} slow={p.get('slow')} band={p.get('band')}",
}


def _load_config(path: str) -> dict:
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="path to a YAML config file")
    parser.add_argument("--synthetic", action="store_true", help="use offline synthetic data")
    parser.add_argument("--kraken-csv", help="path to a Kraken OHLCVT history CSV")
    parser.add_argument("--coinmetrics-csv", help="path to a Coin Metrics community CSV (btc.csv)")
    parser.add_argument("--start", help="optional ISO date to start from, e.g. 2018-01-01")
    parser.add_argument("--strategy", default="mean_reversion",
                        choices=sorted(PARAM_GRIDS), help="strategy to validate")
    parser.add_argument("--train", type=int, default=365, help="train window in bars")
    parser.add_argument("--test", type=int, default=90, help="test window in bars")
    args = parser.parse_args()

    fee_bps, slippage_bps, cash = 26, 5, 10_000
    if args.kraken_csv:
        df = load_kraken_csv(args.kraken_csv)
        print(f"[kraken-csv] {os.path.basename(args.kraken_csv)}: {len(df)} bars")
    elif args.coinmetrics_csv:
        df = load_coinmetrics_csv(args.coinmetrics_csv)
        print(f"[coinmetrics] {os.path.basename(args.coinmetrics_csv)}: {len(df)} bars "
              f"(BTC/USD daily reference rate)")
    elif args.config:
        cfg = _load_config(args.config)
        d = cfg["data"]
        df = load_ohlcv(d["exchange"], d["symbol"], d["timeframe"], d.get("since"),
                        d.get("cache_dir", ".cache"))
        b = cfg.get("backtest", {})
        fee_bps = b.get("fee_bps", 26)
        slippage_bps = b.get("slippage_bps", 5)
        cash = b.get("initial_cash", 10_000)
        print(f"[{d['exchange']}] {d['symbol']} {d['timeframe']}: {len(df)} bars")
    else:
        df = synthetic_ohlcv(n=2000, timeframe="1d")
        print(f"[synthetic] {len(df)} daily bars")

    if args.start:
        df = df[df.index >= pd.Timestamp(args.start, tz="UTC")]
        print(f"filtered from {args.start}: {len(df)} bars")

    grid = PARAM_GRIDS[args.strategy]
    fmt = _FMT[args.strategy]
    bt_kwargs = {"initial_cash": cash, "fee_bps": fee_bps, "slippage_bps": slippage_bps}
    result = walk_forward(
        df, args.strategy, grid,
        train_size=args.train, test_size=args.test,
        metric="sharpe", backtest_kwargs=bt_kwargs,
    )
    print(f"strategy: {args.strategy}")

    print(f"\n=== Walk-forward: {len(result.folds)} folds "
          f"(train={args.train}, test={args.test} bars) ===")
    for i, fold in enumerate(result.folds):
        p = fold["best_params"]
        oos = fold["oos_summary"].get("total_return", float("nan"))
        params_str = fmt(p) if p else "no valid params"
        print(f"  fold {i:>2} | {fold['test_start'].date()}→{fold['test_end'].date()} "
              f"| {params_str:<28} | OOS {oos:+.2%}")

    s = result.summary
    print("\n=== Out-of-sample (stitched, after costs) ===")
    if s:
        print(f"  OOS total return : {s['oos_total_return']:+.2%}")
        print(f"  OOS Sharpe       : {s['oos_sharpe']:.2f}")
        print(f"  OOS Sortino      : {s['oos_sortino']:.2f}")
        print(f"  OOS max drawdown : {s['oos_max_drawdown']:.2%}")
        print(f"  Folds / OOS bars : {s['n_folds']} / {s['oos_bars']}")
        verdict = "SURVIVES (investigate further)" if s["oos_total_return"] > 0 and s["oos_sharpe"] > 0.5 else "REJECTED"
        print(f"\n  Verdict: {verdict}")
    print("\nOut-of-sample results are the honest measure — but still not live performance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
