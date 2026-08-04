"""Z-score mean-reversion strategy.

Idea: over short horizons, price tends to revert toward a rolling mean. We
measure how stretched price is via a z-score of the close against a rolling
mean/std window, then:

    z <= -entry_z   -> go long  (price unusually cheap, expect bounce)
    z >= +entry_z   -> go short (price unusually rich, expect fade)  [if allowed]
    |z| <= exit_z   -> flatten  (reversion has played out)

Between the entry and exit bands the previous position is held (hysteresis),
which avoids churning on every small wiggle around the threshold.

No-lookahead: the rolling window and the resulting z-score for bar t use only
closes up to and including bar t. The engine then executes on bar t+1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class MeanReversion(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        lookback: int = 48,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        allow_short: bool = True,
        cost_bps: float = 0.0,
        edge_safety: float = 1.0,
    ) -> None:
        if lookback < 2:
            raise ValueError("lookback must be >= 2")
        if entry_z <= exit_z:
            raise ValueError("entry_z must be greater than exit_z")
        if edge_safety < 0:
            raise ValueError("edge_safety must be >= 0")
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.allow_short = allow_short
        # Cost gate: an entry is only taken if the *expected* reversion move
        # clears round-trip cost by the safety multiple. cost_bps=0 disables it.
        self.cost_bps = cost_bps
        self.edge_safety = edge_safety

    def zscore(self, close: pd.Series) -> pd.Series:
        mean = close.rolling(self.lookback).mean()
        std = close.rolling(self.lookback).std(ddof=0)
        z = (close - mean) / std.replace(0.0, np.nan)
        return z

    def _clears_cost(self, zi: float, std_i: float, price_i: float) -> bool:
        """True if the expected reversion (|z|*std, back toward the mean) as a
        fraction of price beats round-trip cost * safety margin."""
        if self.cost_bps <= 0:
            return True
        if price_i <= 0 or np.isnan(std_i):
            return False
        expected_move_frac = abs(zi) * std_i / price_i
        hurdle = self.edge_safety * (self.cost_bps / 10_000.0)
        return expected_move_frac >= hurdle

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        z = self.zscore(df["close"])
        std = df["close"].rolling(self.lookback).std(ddof=0)

        target = np.zeros(len(df))
        pos = 0.0
        z_values = z.to_numpy()
        std_values = std.to_numpy()
        close_values = df["close"].to_numpy()
        for i, zi in enumerate(z_values):
            if np.isnan(zi):
                target[i] = 0.0
                continue
            gate = self._clears_cost(zi, std_values[i], close_values[i])
            if pos == 0.0:
                if zi <= -self.entry_z and gate:
                    pos = 1.0
                elif zi >= self.entry_z and self.allow_short and gate:
                    pos = -1.0
            else:
                # hold until reversion completes, then flatten
                if abs(zi) <= self.exit_z:
                    pos = 0.0
                # allow a direct flip if it stretches hard the other way
                elif pos > 0 and zi >= self.entry_z and self.allow_short and gate:
                    pos = -1.0
                elif pos < 0 and zi <= -self.entry_z and gate:
                    pos = 1.0
            target[i] = pos

        signals = pd.Series(target, index=df.index, name="signal")
        return self._validate(signals, df)
