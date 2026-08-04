#!/usr/bin/env python3
"""Build the Positions & Activity page — what the bot is doing now and every
position it has taken historically. Concrete activity, not theory.
"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pandas as pd
from quantbot.data import load_kraken_csv, load_coinmetrics_csv
from quantbot.strategies import build_strategy
from quantbot.risk import volatility_target, drawdown_scale, periodic_rebalance
from quantbot.backtest import run_backtest

UPLOAD = "/root/.claude/uploads/21d9a104-7544-5a84-a67a-5ffba7306c35/07cdd365-XBTUSD_1440.csv"
CM = "/tmp/claude-0/-home-user-Countdown/21d9a104-7544-5a84-a67a-5ffba7306c35/scratchpad/btc_cm.csv"
OUT = "/tmp/claude-0/-home-user-Countdown/21d9a104-7544-5a84-a67a-5ffba7306c35/scratchpad/positions.html"
STATE = os.path.join(os.path.dirname(__file__), "..", "paper_state.json")

k = load_kraken_csv(UPLOAD); k = k[k.index >= pd.Timestamp("2018-01-01", tz="UTC")]
cm = load_coinmetrics_csv(CM, with_onchain=True)[["flow_in", "flow_out", "fee_ntv"]]
df = k.join(cm).dropna(subset=["flow_in", "flow_out", "fee_ntv"])
rets = df["close"].pct_change()
ens = build_strategy("ensemble", factors=("trend", "flow", "fee")).generate_signals(df)
expo = periodic_rebalance(volatility_target(ens, rets, 0.35) * drawdown_scale(df["close"], 0.6), 7, 1 / 3)
res = run_backtest(df, expo, initial_cash=1000, fee_bps=26, slippage_bps=5, annual_cash_yield=0.045)

pos = res.positions          # daily held exposure
trades = res.trades          # one row per position change


def describe(frm, to):
    if frm < 0.01 and to >= 0.01:
        return ("Entered", f"Bought in — now {round(to*100)}% in BTC", "up")
    if to < 0.01 and frm >= 0.01:
        return ("Exited", "Sold to cash — now flat", "down")
    if to > frm:
        return ("Increased", f"Raised to {round(to*100)}% in BTC", "up")
    return ("Reduced", f"Cut to {round(to*100)}% in BTC", "down")


log, prev = [], None
for ts, row in trades.iterrows():
    frm, to = float(row["from_exposure"]), float(row["to_exposure"])
    action, desc, d = describe(frm, to)
    held = (ts - prev).days if prev is not None else None
    log.append({"date": ts.strftime("%Y-%m-%d"), "action": action, "desc": desc,
                "from": round(frm, 3), "to": round(to, 3), "price": round(float(row["price"]), 0),
                "held": held, "dir": d})
    prev = ts
log = list(reversed(log))    # most recent first

ts_list = list(trades.index)
durs = [(ts_list[i + 1] - ts_list[i]).days for i in range(len(ts_list) - 1)]
summary = {
    "changes": len(trades),
    "time_in_market": float((pos > 0.01).mean()),
    "avg_expo": float(pos.mean()),
    "avg_hold": round(sum(durs) / len(durs)) if durs else 0,
    "longest": max(durs) if durs else 0,
    "current_days": (pos.index[-1] - ts_list[-1]).days if ts_list else 0,
    "period": f"{df.index[0].strftime('%b %Y')} – {df.index[-1].strftime('%b %Y')}",
}

# weekly exposure timeline
ex_w = pos.resample("W").last().dropna()

# live paper account
live = {"exposure": 0.0, "signal": 0.0, "price": None, "as_of": None, "trades": [], "since": None}
if os.path.exists(STATE):
    st = json.load(open(STATE)); h = st.get("nav_history", [])
    if h:
        last = h[-1]; nav = last["nav"]
        live.update(exposure=(st["btc"] * last["price"]) / nav if nav else 0.0,
                    signal=last["exposure"], price=last["price"], as_of=last["date"],
                    since=st.get("created_at", "")[:10],
                    trades=[{"date": t["date"], "side": t["side"], "units": t["units"],
                             "price": t["price"]} for t in st.get("trades", [])][::-1])

data = {
    "live": live,
    "history": {
        "dates": [d.strftime("%Y-%m-%d") for d in ex_w.index],
        "expo": [round(float(x), 3) for x in ex_w.values],
        "log": log, "summary": summary,
    },
}
html = open(os.path.join(os.path.dirname(__file__), "positions_template.html")).read()
html = html.replace("/*__DATA__*/null", json.dumps(data))
open(OUT, "w").write(html)
print("wrote", OUT, "| position changes:", len(log), "| time in market", round(summary["time_in_market"]*100), "%")
