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
        """Return a target-exposure series aligned to ``df.index``.

        Values are a fraction of equity in [-1, 1]: discrete strategies use
        {-1, 0, 1} (short/flat/long); ensembles may return anything in between
        to scale position size with conviction. Must not use future bars.
        """
        raise NotImplementedError

    def _validate(self, signals: pd.Series, df: pd.DataFrame) -> pd.Series:
        signals = signals.reindex(df.index).fillna(0.0)
        if signals.max() > 1.0 + 1e-9 or signals.min() < -1.0 - 1e-9:
            raise ValueError(
                f"{self.name}: exposure must be within [-1, 1], got "
                f"[{signals.min():.3f}, {signals.max():.3f}]"
            )
        return signals
