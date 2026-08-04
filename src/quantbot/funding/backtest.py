"""Delta-neutral funding-arbitrage backtester.

Given a series of per-interval funding rates (e.g. every 8h), simulate the carry:
short the perp / long the spot, collect funding each interval, pay trading costs
on entry/exit. Because the position is delta-neutral, spot and perp price moves
cancel and the equity curve is essentially *cumulative funding minus costs* — so
the funding series is the one input that matters.

Two policies:
  always       hold the hedge the whole time; collect positive funding, pay
               negative. One entry cost, then ride it. Simplest, lowest turnover.
  conditional  only hold when funding is above `entry`; step out when it falls
               below `exit`. Dodges negative-funding stretches, but pays a
               round-trip (both legs) on every switch.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class FundingResult:
    equity: pd.Series
    returns: pd.Series          # net per-interval return
    position: pd.Series         # 1 = holding the hedge, 0 = flat
    funding: pd.Series
    switches: int
    initial: float
    periods_per_year: float

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1])


def run_funding_arb(
    funding: pd.Series,
    mode: str = "always",
    entry: float = 0.0,
    exit: float = -1e-4,
    per_leg_bps: float = 5.0,
    initial: float = 1000.0,
    periods_per_year: float = 1095.0,   # 8h funding -> 3/day * 365
) -> FundingResult:
    """Simulate delta-neutral funding capture.

    per_leg_bps  fee per leg per fill; entering or exiting the hedge trades two
                 legs (spot + perp), so each position switch costs 2 x per_leg_bps.
                 Use a small/negative value to model maker rebates.
    """
    funding = funding.astype(float)
    n = len(funding)
    if n == 0:
        raise ValueError("empty funding series")

    pos = np.zeros(n)
    if mode == "always":
        pos[:] = 1.0
    elif mode == "conditional":
        if entry <= exit:
            raise ValueError("entry must be above exit")
        p = 0.0
        for i, f in enumerate(funding.to_numpy()):
            if p == 0.0 and f > entry:
                p = 1.0
            elif p == 1.0 and f < exit:
                p = 0.0
            pos[i] = p
    else:
        raise ValueError("mode must be 'always' or 'conditional'")

    pos = pd.Series(pos, index=funding.index)
    held = pos.shift(1).fillna(0.0)                 # decide at t, hold through t+1
    gross = held * funding                          # short perp collects funding

    switch = pos.diff().abs().fillna(pos.iloc[0])   # 1 on each entry/exit (and initial entry)
    cost = switch * (2.0 * per_leg_bps / 10_000.0)  # two legs per switch
    net = (gross - cost).fillna(0.0)

    equity = (1.0 + net).cumprod() * initial
    return FundingResult(equity, net, pos, funding, int((pos.diff().abs() > 0).sum()),
                         initial, periods_per_year)


def funding_summary(res: FundingResult) -> dict:
    r = res.returns
    n = len(r)
    yrs = n / res.periods_per_year
    total = res.final_equity / res.initial - 1.0
    cagr = (res.final_equity / res.initial) ** (1 / yrs) - 1 if yrs > 0 else 0.0
    sharpe = r.mean() / r.std() * np.sqrt(res.periods_per_year) if r.std() > 0 else 0.0
    dd = (res.equity / res.equity.cummax() - 1).min()
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": float(sharpe),
        "max_drawdown": float(dd),
        "avg_funding_annualized": float(res.funding.mean() * res.periods_per_year),
        "pct_intervals_positive": float((res.funding > 0).mean()),
        "switches": res.switches,
        "final_equity": res.final_equity,
    }
