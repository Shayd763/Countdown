#!/usr/bin/env python3
"""Run walk-forward out-of-sample validation.

This is the honest test: parameters are chosen on each train window and scored
on the next unseen test window, with realistic costs. If the stitched
out-of-sample curve isn't profitable after fees, the strategy is rejected.

    python scripts/run_walkforward.py --synthetic
    python scripts/run_walkforward.py --config config/config.yaml
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quantbot.data import load_ohlcv, synthetic_ohlcv  # noqa: E402
from quantbot.validation import walk_forward  # noqa: E402

# Parameter grid searched within each training window.
PARAM_GRID = {
    "lookback": [20, 40, 60],
    "entry_z": [1.5, 2.0, 2.5],
    "exit_z": [0.25, 0.5],
    "allow_short": [False],       # UK spot-only
    "cost_bps": [52],            # round-trip cost gate (2 x 26 bps taker)
    "edge_safety": [1.5],        # require expected move >= 1.5x round-trip cost
}


def _load_config(path: str) -> dict:
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="path to a YAML config file")
    parser.add_argument("--synthetic", action="store_true", help="use offline synthetic data")
    parser.add_argument("--train", type=int, default=365, help="train window in bars")
    parser.add_argument("--test", type=int, default=90, help="test window in bars")
    args = parser.parse_args()

    if args.synthetic or not args.config:
        df = synthetic_ohlcv(n=2000, timeframe="1d")
        fee_bps, slippage_bps, cash = 26, 5, 10_000
        print(f"[synthetic] {len(df)} daily bars")
    else:
        cfg = _load_config(args.config)
        d = cfg["data"]
        df = load_ohlcv(d["exchange"], d["symbol"], d["timeframe"], d.get("since"),
                        d.get("cache_dir", ".cache"))
        b = cfg.get("backtest", {})
        fee_bps = b.get("fee_bps", 26)
        slippage_bps = b.get("slippage_bps", 5)
        cash = b.get("initial_cash", 10_000)
        print(f"[{d['exchange']}] {d['symbol']} {d['timeframe']}: {len(df)} bars")

    bt_kwargs = {"initial_cash": cash, "fee_bps": fee_bps, "slippage_bps": slippage_bps}
    result = walk_forward(
        df, "mean_reversion", PARAM_GRID,
        train_size=args.train, test_size=args.test,
        metric="sharpe", backtest_kwargs=bt_kwargs,
    )

    print(f"\n=== Walk-forward: {len(result.folds)} folds "
          f"(train={args.train}, test={args.test} bars) ===")
    for i, fold in enumerate(result.folds):
        p = fold["best_params"]
        oos = fold["oos_summary"].get("total_return", float("nan"))
        params_str = (f"lb={p.get('lookback')} ez={p.get('entry_z')} xz={p.get('exit_z')}"
                      if p else "no valid params")
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
