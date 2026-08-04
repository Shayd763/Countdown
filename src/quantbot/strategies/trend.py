"""Trend-following via moving-average crossover.

Where mean-reversion bets that stretched prices snap back, trend-following bets
the opposite: that moves persist. Its appeal for a cost-constrained UK spot
trader is **low turnover** — it holds a position for as long as the trend runs,
so it pays the fee/spread hurdle far less often than a mean-reversion bot that
flips constantly.

Signal: compare a fast and a slow moving average.
    fast > slow  -> long  (uptrend)
    fast < slow  -> short (downtrend)   [only if allow_short; UK spot = long/flat]
    warmup       -> flat

An optional ``band`` adds hysteresis: the fast MA must clear the slow MA by
``band`` (as a fraction) before flipping, which cuts whipsaw churn — and every
avoided flip is a saved round-trip cost.

No-lookahead: MAs at bar t use closes up to t; the engine executes at t+1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class TrendFollowing(Strategy):
    name = "trend"

    def __init__(
        self,
        fast: int = 20,
        slow: int = 100,
        band: float = 0.0,
        allow_short: bool = True,
    ) -> None:
        if fast < 1 or slow < 2:
            raise ValueError("fast >= 1 and slow >= 2 required")
        if fast >= slow:
            raise ValueError("fast window must be shorter than slow window")
        if band < 0:
            raise ValueError("band must be >= 0")
        self.fast = fast
        self.slow = slow
        self.band = band
        self.allow_short = allow_short

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        fast_ma = close.rolling(self.fast).mean()
        slow_ma = close.rolling(self.slow).mean()

        # Relative gap between the MAs; NaN during warmup.
        gap = (fast_ma - slow_ma) / slow_ma.replace(0.0, np.nan)

        target = np.zeros(len(df))
        pos = 0.0
        gap_values = gap.to_numpy()
        for i, g in enumerate(gap_values):
            if np.isnan(g):
                target[i] = 0.0
                continue
            if g > self.band:
                pos = 1.0
            elif g < -self.band:
                pos = -1.0 if self.allow_short else 0.0
            # inside the band: hold the current position (hysteresis)
            target[i] = pos

        signals = pd.Series(target, index=df.index, name="signal")
        return self._validate(signals, df)
