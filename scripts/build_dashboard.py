#!/usr/bin/env python3
"""Build the self-contained HTML performance dashboard.

Computes the strategy's real backtested track record (Kraken BTC/USD + Coin
Metrics on-chain, 2018+) and the live paper-account status, embeds them as JSON,
and writes a static, interactive dashboard page (inline CSS/JS/SVG charts).
"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pandas as pd
from quantbot.data import load_kraken_csv, load_coinmetrics_csv
from quantbot.strategies import build_strategy
from quantbot.risk import volatility_target, drawdown_scale, periodic_rebalance
from quantbot.backtest import run_backtest, performance_summary

UPLOAD = "/root/.claude/uploads/21d9a104-7544-5a84-a67a-5ffba7306c35/07cdd365-XBTUSD_1440.csv"
CM = "/tmp/claude-0/-home-user-Countdown/21d9a104-7544-5a84-a67a-5ffba7306c35/scratchpad/btc_cm.csv"
OUT = "/tmp/claude-0/-home-user-Countdown/21d9a104-7544-5a84-a67a-5ffba7306c35/scratchpad/dashboard.html"
STATE = os.path.join(os.path.dirname(__file__), "..", "paper_state.json")

# ---- compute backtest track record ----
k = load_kraken_csv(UPLOAD)
k = k[k.index >= pd.Timestamp("2018-01-01", tz="UTC")]
cm = load_coinmetrics_csv(CM, with_onchain=True)[["flow_in", "flow_out", "fee_ntv"]]
df = k.join(cm).dropna(subset=["flow_in", "flow_out", "fee_ntv"])
rets = df["close"].pct_change()
ens = build_strategy("ensemble", factors=("trend", "flow", "fee")).generate_signals(df)
expo = periodic_rebalance(volatility_target(ens, rets, 0.35) * drawdown_scale(df["close"], 0.6), 7, 1 / 3)

res = run_backtest(df, expo, initial_cash=1000, fee_bps=26, slippage_bps=5, annual_cash_yield=0.045)
hold = run_backtest(df, pd.Series(1.0, index=df.index), initial_cash=1000, fee_bps=26, slippage_bps=5)
sm, hm = performance_summary(res), performance_summary(hold)

se = res.equity_curve.resample("W").last().dropna()
idx = se.index
he = hold.equity_curve.resample("W").last().reindex(idx).ffill()
sdd = (res.equity_curve / res.equity_curve.cummax() - 1).resample("W").last().reindex(idx)
hdd = (hold.equity_curve / hold.equity_curve.cummax() - 1).resample("W").last().reindex(idx)
ex = res.positions.resample("W").last().reindex(idx).fillna(0.0)

# ---- live paper status ----
live = {"balance": 1000.0, "ret": 0.0, "exposure": 0.0, "signal": 0.0,
        "price": None, "as_of": None, "steps": 0, "trades": 0}
if os.path.exists(STATE):
    st = json.load(open(STATE))
    h = st.get("nav_history", [])
    if h:
        last = h[-1]
        nav = last["nav"]
        live.update(balance=nav, ret=nav / st["initial_capital"] - 1.0,
                    exposure=(st["btc"] * last["price"]) / nav if nav else 0.0,
                    signal=last["exposure"], price=last["price"], as_of=last["date"],
                    steps=len(h), trades=len(st.get("trades", [])))

data = {
    "dates": [d.strftime("%Y-%m-%d") for d in idx],
    "stratEq": [round(float(x), 2) for x in se.values],
    "holdEq": [round(float(x), 2) for x in he.values],
    "stratDD": [round(float(x), 4) for x in sdd.values],
    "holdDD": [round(float(x), 4) for x in hdd.values],
    "expo": [round(float(x), 3) for x in ex.values],
    "metrics": {"cagr": sm["cagr"], "sharpe": sm["sharpe"], "maxdd": sm["max_drawdown"],
                "calmar": sm["cagr"] / abs(sm["max_drawdown"]), "total": sm["total_return"],
                "trades": sm["n_trades"], "final": sm["final_equity"]},
    "hold": {"cagr": hm["cagr"], "sharpe": hm["sharpe"], "maxdd": hm["max_drawdown"],
             "total": hm["total_return"], "final": hm["final_equity"]},
    "live": live,
    "period": f"{idx[0].strftime('%b %Y')} – {idx[-1].strftime('%b %Y')}",
}

html = open(os.path.join(os.path.dirname(__file__), "dashboard_template.html")).read()
html = html.replace("/*__DATA__*/null", json.dumps(data))
with open(OUT, "w") as f:
    f.write(html)
print("wrote", OUT, "| strat Sharpe", round(sm["sharpe"], 2), "| points", len(idx))
