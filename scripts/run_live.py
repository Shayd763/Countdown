#!/usr/bin/env python3
"""Live trading loop — one run = one daily decision.

    fetch data -> compute today's target exposure -> reconcile vs holdings ->
    (maybe) place order -> log

SAFE BY DEFAULT: runs in dry-run mode (no orders) unless you pass --mode paper
or --mode live. Live mode also needs EXCHANGE_API_KEY / EXCHANGE_API_SECRET and
is the only path that moves real money.

Typical use (schedule once per day, e.g. cron at 00:05 UTC):
    python scripts/run_live.py --mode dry-run          # see the signal, trade nothing
    python scripts/run_live.py --mode paper            # simulate fills, build a record
    python scripts/run_live.py --mode live             # real orders (keys required)

Kill-switch: create a file named KILLSWITCH in the working dir to halt all trading.
Deep history: pass --kraken-csv XBTUSD_1440.csv so the 200-day signals are warm.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

from quantbot.data import load_kraken_csv  # noqa: E402
from quantbot.live import (  # noqa: E402
    ExecutionConfig, StrategyConfig, decide, make_broker, plan_order,
)


def _log(obj: dict) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **obj}))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", default="dry-run", choices=["dry-run", "paper", "live"])
    ap.add_argument("--pair", default="XXBTZUSD", help="Kraken pair (XXBTZGBP for BTC/GBP)")
    ap.add_argument("--kraken-csv", help="deep-history CSV to warm up the 200-day signals")
    ap.add_argument("--held-exposure", type=float, default=None,
                    help="override current exposure (else read from broker)")
    args = ap.parse_args()

    strat = StrategyConfig()
    ex = ExecutionConfig(pair=args.pair, mode=args.mode)

    # 0. Kill-switch: a hard stop that beats everything.
    if os.path.exists(ex.killswitch_file):
        _log({"event": "halted", "reason": f"kill-switch file '{ex.killswitch_file}' present"})
        return 0

    # 1. Fresh data (Kraken price + Coin Metrics on-chain), warmed by deep history.
    try:
        from quantbot.live.feeds import build_dataset
        history = load_kraken_csv(args.kraken_csv) if args.kraken_csv else None
        df = build_dataset(history=history, pair=args.pair)
    except Exception as e:  # network / data failure -> do nothing, never trade blind
        _log({"event": "data_error", "error": str(e), "action": "no trade"})
        return 1

    # 2. Staleness guard: refuse to act on old data.
    age_h = (pd.Timestamp.now(tz="UTC") - df.index[-1]).total_seconds() / 3600
    if age_h > ex.max_stale_hours:
        _log({"event": "stale_data", "latest": str(df.index[-1].date()),
              "age_hours": round(age_h, 1), "action": "no trade"})
        return 1

    # 3. Portfolio + current exposure.
    broker = make_broker(ex)
    price = float(df["close"].iloc[-1])
    pf = broker.get_portfolio(price)
    held = args.held_exposure if args.held_exposure is not None else pf.exposure

    # 4. Decide + size (with safety rails) + execute.
    decision = decide(df, held, strat)
    result = "no trade (policy)"
    if decision.should_trade:
        plan = plan_order(pf, decision.target_exposure, ex)
        result = broker.execute(plan)

    _log({"event": "decision", "mode": args.mode, "price": round(price, 2),
          **decision.as_dict(), "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
