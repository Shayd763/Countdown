#!/usr/bin/env python3
"""Paper-trading harness — runs the validated strategy on fresh data with a
persistent, committed track record. No real money; safe to run anywhere.

Each run: fetch latest BTC data (Coin Metrics — reachable without keys), compute
today's target exposure, apply the weekly-rebalance policy, simulate the fill
with realistic costs, and append to a persistent paper portfolio (paper_state.json).
The state file is committed to the repo so the record survives ephemeral runners.

Data note: uses Coin Metrics' daily reference price (the sandbox can't reach
Kraken's live API). That's a fine proxy for a paper record — the strategy was
shown to behave the same on real Kraken prices. Swap to scripts/run_live.py on a
networked machine for venue-native prices.

    python scripts/paper_trade.py            # one paper step on the latest data
    python scripts/paper_trade.py --reset    # start a fresh paper portfolio
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

from quantbot.live import ExecutionConfig, Portfolio, StrategyConfig, decide, plan_order  # noqa: E402

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "paper_state.json")
CM_URL = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv"
INITIAL_CAPITAL = 1000.0


def fetch_data() -> pd.DataFrame:
    raw = urllib.request.urlopen(CM_URL, timeout=90).read()  # noqa: S310
    cols = ["time", "PriceUSD", "FlowInExNtv", "FlowOutExNtv", "FeeTotNtv"]
    d = pd.read_csv(io.BytesIO(raw), usecols=cols).dropna()
    idx = pd.to_datetime(d["time"], utc=True)
    p = d["PriceUSD"].astype(float).to_numpy()
    return pd.DataFrame({
        "open": p, "high": p, "low": p, "close": p, "volume": 0.0,
        "flow_in": d["FlowInExNtv"].astype(float).to_numpy(),
        "flow_out": d["FlowOutExNtv"].astype(float).to_numpy(),
        "fee_ntv": d["FeeTotNtv"].astype(float).to_numpy(),
    }, index=idx).sort_index()


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"initial_capital": INITIAL_CAPITAL, "cash": INITIAL_CAPITAL, "btc": 0.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_data_date": None, "trades": [], "nav_history": []}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true", help="start a fresh paper portfolio")
    args = ap.parse_args()

    strat, ex = StrategyConfig(), ExecutionConfig(mode="paper")
    if args.reset and os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

    if os.path.exists(ex.killswitch_file):
        print(json.dumps({"event": "halted", "reason": "kill-switch present"}))
        return 0

    df = fetch_data()
    state = load_state()
    data_date = str(df.index[-1].date())
    price = float(df["close"].iloc[-1])

    if state["last_data_date"] == data_date:
        print(json.dumps({"event": "skip", "reason": f"already processed {data_date}"}))
        return 0

    pf = Portfolio(quote=state["cash"], base=state["btc"], price=price)
    held = pf.exposure
    fresh = not state["trades"]                       # first-ever run establishes the position
    decision = decide(df, held, strat)
    should = decision.should_trade or (fresh and decision.target_exposure > 0)

    traded = None
    if should:
        plan = plan_order(pf, decision.target_exposure, ex)
        if plan.side != "none":
            cost = plan.quote_value * (strat.fee_bps + strat.slippage_bps) / 10_000.0
            signed = plan.base_units if plan.side == "buy" else -plan.base_units
            state["btc"] += signed
            state["cash"] -= signed * price + cost
            traded = {"date": data_date, "side": plan.side, "units": round(plan.base_units, 8),
                      "price": round(price, 2), "cost": round(cost, 2)}
            state["trades"].append(traded)

    nav = state["cash"] + state["btc"] * price
    state["last_data_date"] = data_date
    state["nav_history"].append({"date": data_date, "nav": round(nav, 2),
                                 "exposure": round(decision.target_exposure, 3), "price": round(price, 2)})
    save_state(state)

    ret = nav / state["initial_capital"] - 1.0
    print(json.dumps({
        "event": "paper_step", "data_date": data_date, "price": round(price, 2),
        "target_exposure": round(decision.target_exposure, 3), "held_exposure": round(held, 3),
        "traded": traded, "nav": round(nav, 2), "total_return": f"{ret:+.2%}",
        "btc": round(state["btc"], 8), "cash": round(state["cash"], 2),
        "reason": decision.reason,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
