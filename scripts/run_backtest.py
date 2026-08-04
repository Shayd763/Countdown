#!/usr/bin/env python3
"""Run a backtest from a YAML config (or the built-in synthetic dataset).

Examples
--------
    # Offline smoke test on synthetic mean-reverting data:
    python scripts/run_backtest.py --synthetic

    # Real data + strategy from a config file:
    python scripts/run_backtest.py --config config/config.yaml
"""

from __future__ import annotations

import argparse
import os
import sys

# Make `import quantbot` work without installation.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quantbot.backtest import performance_summary, run_backtest  # noqa: E402
from quantbot.data import load_ohlcv, synthetic_ohlcv  # noqa: E402
from quantbot.strategies import build_strategy  # noqa: E402


def _load_config(path: str) -> dict:
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="path to a YAML config file")
    parser.add_argument("--synthetic", action="store_true", help="use offline synthetic data")
    args = parser.parse_args()

    if args.synthetic or not args.config:
        df = synthetic_ohlcv()
        strat = build_strategy("mean_reversion", lookback=48, entry_z=2.0, exit_z=0.5)
        bt_cfg = {"initial_cash": 10_000, "fee_bps": 10, "slippage_bps": 5}
        print(f"[synthetic] {len(df)} bars of mean-reverting data")
    else:
        cfg = _load_config(args.config)
        d = cfg["data"]
        df = load_ohlcv(d["exchange"], d["symbol"], d["timeframe"], d.get("since"),
                        d.get("cache_dir", ".cache"))
        s = cfg["strategy"]
        strat = build_strategy(s["name"], **s.get("params", {}))
        bt_cfg = cfg.get("backtest", {})
        print(f"[{d['exchange']}] {d['symbol']} {d['timeframe']}: {len(df)} bars")

    signals = strat.generate_signals(df)
    result = run_backtest(
        df, signals,
        initial_cash=bt_cfg.get("initial_cash", 10_000),
        fee_bps=bt_cfg.get("fee_bps", 10),
        slippage_bps=bt_cfg.get("slippage_bps", 5),
    )
    summary = performance_summary(result)

    print("\n=== Performance ===")
    print(f"  Total return : {summary['total_return']:+.2%}")
    print(f"  CAGR         : {summary['cagr']:+.2%}")
    print(f"  Sharpe       : {summary['sharpe']:.2f}")
    print(f"  Sortino      : {summary['sortino']:.2f}")
    print(f"  Max drawdown : {summary['max_drawdown']:.2%}")
    print(f"  Win rate     : {summary['win_rate']:.2%}")
    print(f"  Trades       : {summary['n_trades']}")
    print(f"  Total costs  : {summary['total_costs']:.2f}")
    print(f"  Final equity : {summary['final_equity']:.2f}")
    print("\nRisk reminder: synthetic/backtested results are not live performance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
