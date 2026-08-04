"""Performance metrics.

These are the numbers that decide whether a strategy lives or dies. Return
alone is meaningless without the risk taken to get it, so we lead with
risk-adjusted measures and drawdown.

Bars-per-year is inferred from the index spacing so Sharpe/Sortino annualise
correctly whether the data is hourly, daily, or minute.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_SECONDS_PER_YEAR = 365.25 * 24 * 3600


def _bars_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 3:
        return 252.0
    # Use Timedelta seconds so we're agnostic to the index's time resolution
    # (pandas 2.x indexes can be ns/us/ms — raw int views differ by unit).
    median = index.to_series().diff().dropna().median()
    seconds = median.total_seconds()
    if seconds <= 0:
        return 252.0
    return _SECONDS_PER_YEAR / seconds


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline as a negative fraction (e.g. -0.23)."""
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def sharpe_ratio(returns: pd.Series, bars_per_year: float) -> float:
    std = returns.std(ddof=0)
    if std == 0 or np.isnan(std):
        return 0.0
    return float(returns.mean() / std * np.sqrt(bars_per_year))


def sortino_ratio(returns: pd.Series, bars_per_year: float) -> float:
    downside = returns[returns < 0]
    dstd = downside.std(ddof=0)
    if dstd == 0 or np.isnan(dstd):
        return 0.0
    return float(returns.mean() / dstd * np.sqrt(bars_per_year))


def performance_summary(result) -> dict:
    """Compute a headline metrics dict from a BacktestResult."""
    equity = result.equity_curve
    returns = result.returns
    bpy = _bars_per_year(equity.index)

    n_bars = len(equity)
    years = n_bars / bpy if bpy else 0.0
    total_return = result.total_return
    cagr = (equity.iloc[-1] / result.initial_cash) ** (1 / years) - 1 if years > 0 else 0.0

    trades = result.trades
    n_trades = int(len(trades))
    total_costs = float(trades["cost"].sum()) if n_trades else 0.0

    # Per-bar returns while actually in a position tell us the hit rate.
    active = returns[result.positions.shift(1).fillna(0.0) != 0.0]
    win_rate = float((active > 0).mean()) if len(active) else 0.0

    return {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe_ratio(returns, bpy),
        "sortino": sortino_ratio(returns, bpy),
        "max_drawdown": max_drawdown(equity),
        "n_trades": n_trades,
        "win_rate": win_rate,
        "total_costs": total_costs,
        "final_equity": result.final_equity,
        "bars": n_bars,
    }
