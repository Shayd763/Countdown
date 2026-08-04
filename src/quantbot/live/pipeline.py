"""Pure signal core — turn a price+on-chain history into today's target exposure.

This module is deliberately free of any network or exchange code so it can be
unit-tested offline and reused identically in backtest and live. Given a frame
with columns [close, flow_in, flow_out, fee_ntv], it produces the same exposure
the backtest used: ensemble(trend,flow,fee) -> vol-target -> drawdown de-risk,
quantised. The rebalance policy (when to actually trade) is applied on top.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..risk import drawdown_scale, volatility_target
from ..strategies import MultiFactorEnsemble
from .config import StrategyConfig


def target_exposure_series(df: pd.DataFrame, cfg: StrategyConfig) -> pd.Series:
    """Full history of the strategy's *raw daily* target exposure in [0, 1]
    (before the weekly rebalance gate). Uses only past data at each bar."""
    ens = MultiFactorEnsemble(
        factors=cfg.factors, trend_ma=cfg.trend_ma,
        flow_window=cfg.flow_window, flow_long=cfg.flow_long, flow_exit=cfg.flow_exit,
        fee_fast=cfg.fee_fast, fee_slow=cfg.fee_slow,
    ).generate_signals(df)
    vt = volatility_target(ens, df["close"].pct_change(), cfg.target_vol, cfg.vol_window)
    expo = vt * drawdown_scale(df["close"], cfg.dd_tolerance)
    quantised = np.round(expo / cfg.quant) * cfg.quant
    return quantised.clip(0.0, 1.0)


@dataclass
class Decision:
    date: pd.Timestamp
    target_exposure: float      # what the strategy wants today, [0, 1]
    held_exposure: float        # what you currently hold, [0, 1]
    is_rebalance_day: bool
    should_trade: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "date": str(self.date.date()),
            "target_exposure": round(self.target_exposure, 4),
            "held_exposure": round(self.held_exposure, 4),
            "is_rebalance_day": self.is_rebalance_day,
            "should_trade": self.should_trade,
            "reason": self.reason,
        }


def decide(df: pd.DataFrame, held_exposure: float, cfg: StrategyConfig) -> Decision:
    """Compute today's target and whether to trade under the rebalance policy.

    Trade only on the configured weekday AND only if the target differs from the
    currently-held exposure by more than ``rebalance_band`` — this is what keeps
    turnover to ~9-15 trades/year and stops churn on tiny signal wiggles.
    """
    exposure = target_exposure_series(df, cfg)
    today = df.index[-1]
    target = float(exposure.iloc[-1])
    is_reb = today.weekday() == cfg.rebalance_weekday
    drift = abs(target - held_exposure)

    if not is_reb:
        should, reason = False, f"not a rebalance day (weekday={today.weekday()})"
    elif drift < cfg.rebalance_band:
        should, reason = False, f"within band (drift {drift:.2f} < {cfg.rebalance_band})"
    else:
        should, reason = True, f"rebalance: target {target:.2f} vs held {held_exposure:.2f}"

    return Decision(today, target, held_exposure, is_reb, should, reason)
