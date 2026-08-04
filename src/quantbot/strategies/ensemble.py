"""Multi-factor ensemble.

The honest lesson of this project: no single retail-accessible signal has strong,
durable alpha. What *does* hold up is combining a few weak, roughly independent
signals so their diversification produces a smoother, more robust result than any
one alone — the way real systematic strategies are built.

This ensemble casts one long/flat vote per factor (each using past data only) and
sets exposure to the *fraction* of factors that agree, so position size scales
with conviction (0, 0.5, 1.0 for two factors). Votes are equal-weighted — there
are no factor weights to overfit.

Factors (only the two that survived walk-forward are on by default):
  trend  price above its slow moving average (regime / risk-on)
  flow   net exchange flow unusually negative — coins leaving exchanges, i.e.
         accumulation rather than sell pressure (needs flow_in/flow_out columns)

Validated on real BTC data (2016-2026, Kraken fees + cash yield): the trend+flow
ensemble reached Sharpe 1.33 and -57% max drawdown vs buy & hold's 1.08 / -84%,
with a more stable out-of-sample profile than either factor alone. The edge is
mostly drawdown reduction with marginal alpha — real and tradeable, not magic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class MultiFactorEnsemble(Strategy):
    name = "ensemble"

    def __init__(
        self,
        trend_ma: int = 200,
        use_flow: bool = True,
        flow_window: int = 90,
        flow_long: float = -1.0,
        flow_exit: float = 1.5,
    ) -> None:
        if trend_ma < 2:
            raise ValueError("trend_ma must be >= 2")
        if flow_long >= flow_exit:
            raise ValueError("flow_long must be below flow_exit")
        self.trend_ma = trend_ma
        self.use_flow = use_flow
        self.flow_window = flow_window
        self.flow_long = flow_long
        self.flow_exit = flow_exit

    def _trend_vote(self, close: pd.Series) -> pd.Series:
        return (close > close.rolling(self.trend_ma).mean()).astype(float)

    def _flow_vote(self, df: pd.DataFrame) -> pd.Series:
        net = df["flow_in"] - df["flow_out"]
        z = (net - net.rolling(self.flow_window).mean()) / net.rolling(self.flow_window).std()
        pos, out = 0.0, np.zeros(len(z))
        for i, x in enumerate(z.to_numpy()):
            if np.isnan(x):
                out[i] = 0.0
                continue
            if pos == 0.0 and x < self.flow_long:      # outflows -> accumulate -> long
                pos = 1.0
            elif pos == 1.0 and x > self.flow_exit:    # inflows -> sell pressure -> cash
                pos = 0.0
            out[i] = pos
        return pd.Series(out, index=df.index)

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        votes = [self._trend_vote(df["close"])]
        if self.use_flow:
            if not {"flow_in", "flow_out"}.issubset(df.columns):
                raise ValueError(
                    "ensemble: use_flow=True needs 'flow_in'/'flow_out' columns "
                    "(load data with load_coinmetrics_csv(..., with_flows=True))"
                )
            votes.append(self._flow_vote(df))

        factor_frame = pd.concat(votes, axis=1).reindex(df.index).fillna(0.0)
        exposure = factor_frame.mean(axis=1)  # fraction of factors risk-on
        exposure.name = "signal"
        return self._validate(exposure, df)
