"""Event-simulated backtest engine.

Design priorities, in order: (1) don't lie to yourself, (2) don't lie to
yourself, (3) speed. The two most common ways a backtest flatters a strategy
are *lookahead bias* and *ignoring costs*; this engine is built to avoid both.

Mechanics
---------
* A strategy emits a target position for bar ``t`` using data up to ``t``.
* We execute the change into that target at the **open of bar t+1**. A signal
  can never trade on its own bar's close.
* Every position change pays a fee and slippage on the traded notional.
* Equity is marked to market each bar on the close.

The result is deliberately conservative. If a strategy only survives with
zero costs and same-bar execution, it does not survive.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    equity_curve: pd.Series      # marked-to-market equity per bar
    returns: pd.Series           # per-bar equity returns
    positions: pd.Series         # position actually held during each bar
    trades: pd.DataFrame         # one row per position change
    initial_cash: float
    fee_bps: float
    slippage_bps: float

    @property
    def final_equity(self) -> float:
        return float(self.equity_curve.iloc[-1])

    @property
    def total_return(self) -> float:
        return self.final_equity / self.initial_cash - 1.0


def run_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    initial_cash: float = 10_000.0,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    size: float | pd.Series = 1.0,
    annual_cash_yield: float = 0.0,
) -> BacktestResult:
    """Simulate a strategy on OHLCV data.

    Parameters
    ----------
    df                 OHLCV frame (needs 'open' and 'close').
    signals            target position per bar in {-1, 0, 1}, aligned to df.index.
    size               fraction of equity to deploy when in a position (scalar or
                       a per-bar Series from the risk layer). Exposure = signal*size.
    annual_cash_yield  interest earned on idle cash (e.g. 0.045 for a 4.5% T-bill
                       / money-market rate). Matters for long/flat strategies that
                       sit in cash when out of the market — that cash isn't dead,
                       it earns the risk-free rate. Accrued per bar on positive cash.
    """
    if not {"open", "close"}.issubset(df.columns):
        raise ValueError("df must contain 'open' and 'close' columns")

    signals = signals.reindex(df.index).fillna(0.0)
    size_series = (
        pd.Series(size, index=df.index) if np.isscalar(size)
        else size.reindex(df.index).fillna(0.0)
    )

    # Desired exposure decided on bar t, put into effect at t+1 open.
    target_exposure = (signals * size_series).shift(1).fillna(0.0)

    open_ = df["open"].to_numpy()
    close = df["close"].to_numpy()
    target = target_exposure.to_numpy()

    cost_rate = (fee_bps + slippage_bps) / 10_000.0

    # Per-bar interest factor for idle cash, from the annual yield and bar spacing.
    per_bar_yield = 0.0
    if annual_cash_yield:
        from .metrics import _bars_per_year

        bpy = _bars_per_year(df.index)
        per_bar_yield = (1.0 + annual_cash_yield) ** (1.0 / bpy) - 1.0

    n = len(df)
    equity = np.empty(n)
    held = np.empty(n)          # exposure fraction held through bar i
    cash = initial_cash
    units = 0.0                 # signed units of the asset
    prev_exposure = 0.0
    trade_rows = []

    for i in range(n):
        eq_before = cash + units * open_[i]
        desired = target[i]

        if not np.isclose(desired, prev_exposure):
            # Rebalance at this bar's open to the new target exposure.
            desired_units = desired * eq_before / open_[i] if open_[i] > 0 else 0.0
            traded_units = desired_units - units
            traded_notional = abs(traded_units) * open_[i]
            cost = traded_notional * cost_rate

            cash -= traded_units * open_[i]  # buy: cash down; sell: cash up
            cash -= cost
            units = desired_units

            trade_rows.append(
                {
                    "timestamp": df.index[i],
                    "price": open_[i],
                    "from_exposure": prev_exposure,
                    "to_exposure": desired,
                    "traded_notional": traded_notional,
                    "cost": cost,
                }
            )
            prev_exposure = desired

        # Idle cash held through the bar (post-trade) earns the risk-free rate.
        if per_bar_yield and cash > 0:
            cash *= 1.0 + per_bar_yield

        # Mark to market on the close.
        equity[i] = cash + units * close[i]
        held[i] = prev_exposure

    equity_curve = pd.Series(equity, index=df.index, name="equity")
    returns = equity_curve.pct_change().fillna(0.0)
    positions = pd.Series(held, index=df.index, name="position")
    trades = pd.DataFrame(trade_rows)
    if not trades.empty:
        trades = trades.set_index("timestamp")

    return BacktestResult(
        equity_curve=equity_curve,
        returns=returns,
        positions=positions,
        trades=trades,
        initial_cash=initial_cash,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
