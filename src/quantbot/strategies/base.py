"""Strategy interface.

A strategy maps an OHLCV frame to a *target position* series: one value per
bar in {-1, 0, +1} (short / flat / long). The backtest engine is responsible
for turning target positions into orders, applying costs, and — critically —
executing on the *next* bar so a signal never trades on information from its
own bar.

The no-lookahead contract:
    signal for bar t may use data from bars <= t only.
The engine enforces execution timing; strategies must honour the data rule.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    #: human-readable name, overridden by subclasses
    name: str = "base"

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Return a target-position series aligned to ``df.index``.

        Values must be in {-1, 0, 1}. Must not use future bars.
        """
        raise NotImplementedError

    def _validate(self, signals: pd.Series, df: pd.DataFrame) -> pd.Series:
        signals = signals.reindex(df.index).fillna(0.0)
        bad = set(pd.unique(signals.round(6))) - {-1.0, 0.0, 1.0}
        if bad:
            raise ValueError(f"{self.name}: signals must be in {{-1,0,1}}, got {bad}")
        return signals
