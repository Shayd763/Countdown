"""Risk overlays — transform a raw exposure series to control *risk*, not predict
direction.

The honest finding of this project: retail alpha is scarce, but risk reduction is
achievable and robust. These overlays act on any strategy's exposure to cut the
left tail and the trading footprint:

  volatility_target  scale exposure down when realized volatility is high (crashes
                     cluster in high-vol regimes), so risk-per-unit-time is steadier.
                     Cuts drawdowns; costs a little upside.

  periodic_rebalance only let exposure change every N bars (and optionally quantise
                     it), which cuts trade frequency and turnover with little effect
                     on a slow strategy.

On BTC (2018+) the ensemble + weekly-rebalanced vol-target cut max drawdown from
-53% to -46%, roughly halved trade frequency, and lowered average exposure — at
essentially unchanged Sharpe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def realized_vol(returns: pd.Series, window: int = 30, periods_per_year: int = 365) -> pd.Series:
    """Annualised rolling realised volatility of a return series."""
    return returns.rolling(window).std() * np.sqrt(periods_per_year)


def volatility_target(
    exposure: pd.Series,
    returns: pd.Series,
    target_vol: float = 0.50,
    window: int = 30,
    max_leverage: float = 1.0,
    periods_per_year: int = 365,
) -> pd.Series:
    """Scale ``exposure`` so realised risk targets ``target_vol`` (annualised).

    scalar = target_vol / realised_vol, capped at ``max_leverage`` (default 1.0 =
    never lever up, only de-risk). When vol is high the position shrinks; when calm
    it approaches full size. Preserves the sign of the exposure.
    """
    if target_vol <= 0:
        raise ValueError("target_vol must be positive")
    rv = realized_vol(returns, window, periods_per_year)
    scalar = (target_vol / rv).clip(upper=max_leverage).fillna(0.0)
    scaled = exposure * scalar
    return scaled.clip(lower=-max_leverage, upper=max_leverage)


def periodic_rebalance(
    exposure: pd.Series,
    every: int = 7,
    quant: float | None = None,
) -> pd.Series:
    """Reduce trading: only allow exposure to change every ``every`` bars, and
    optionally quantise it to steps of ``quant`` (so tiny changes don't trade).

    Between rebalance bars the previous exposure is held. ``every=1`` with no
    ``quant`` is a no-op.
    """
    if every < 1:
        raise ValueError("every must be >= 1")
    q = exposure
    if quant:
        q = np.round(q / quant) * quant
    if every > 1:
        allow = pd.Series(np.arange(len(q)) % every == 0, index=q.index)
        q = q.where(allow).ffill().fillna(0.0)
    return q
