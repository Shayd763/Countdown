"""Walk-forward validation — the anti-overfitting core.

The cardinal sin of strategy research is judging a strategy on the same data
you used to choose its parameters. Walk-forward fixes that:

    |--- train ---|--- test ---|
                  |--- train ---|--- test ---|
                                |--- train ---|--- test ---|

On each fold we pick the best parameters on the *train* window, then score them
on the immediately following *test* window the strategy has never seen. Stitch
all the out-of-sample test segments together and you get an equity curve that
reflects how the strategy would actually have traded live — decisions made only
on past data. If *that* curve isn't profitable after costs, the strategy is
dead, and we found out cheaply. That is the whole point.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..backtest import performance_summary, run_backtest
from ..strategies import build_strategy


def _score(result, metric: str) -> float:
    summary = performance_summary(result)
    if metric not in summary:
        raise KeyError(f"Unknown metric {metric!r}. Options: {sorted(summary)}")
    return summary[metric]


def grid_search(
    df: pd.DataFrame,
    strategy_name: str,
    param_grid: dict[str, list],
    metric: str = "sharpe",
    backtest_kwargs: dict | None = None,
) -> tuple[dict, float]:
    """Exhaustively evaluate the parameter grid on ``df``; return the best
    (params, score) by ``metric``. Used *inside* a training window only."""
    backtest_kwargs = backtest_kwargs or {}
    keys = list(param_grid)
    best_params: dict = {}
    best_score = -np.inf

    for combo in itertools.product(*(param_grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        try:
            strat = build_strategy(strategy_name, **params)
            signals = strat.generate_signals(df)
            result = run_backtest(df, signals, **backtest_kwargs)
            score = _score(result, metric)
        except (ValueError, KeyError):
            continue  # invalid combo (e.g. entry_z <= exit_z) — skip it
        if np.isfinite(score) and score > best_score:
            best_score = score
            best_params = params

    return best_params, best_score


@dataclass
class WalkForwardResult:
    folds: list[dict] = field(default_factory=list)   # per-fold params + OOS metrics
    oos_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    oos_equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    initial_cash: float = 10_000.0

    @property
    def summary(self) -> dict:
        """Aggregate out-of-sample metrics across all stitched test segments."""
        if self.oos_equity.empty:
            return {}
        # Reuse the metrics engine via a lightweight result-like shim.
        from ..backtest.metrics import max_drawdown, sharpe_ratio, sortino_ratio, _bars_per_year

        bpy = _bars_per_year(self.oos_equity.index)
        return {
            "oos_total_return": float(self.oos_equity.iloc[-1] / self.initial_cash - 1.0),
            "oos_sharpe": sharpe_ratio(self.oos_returns, bpy),
            "oos_sortino": sortino_ratio(self.oos_returns, bpy),
            "oos_max_drawdown": max_drawdown(self.oos_equity),
            "n_folds": len(self.folds),
            "oos_bars": len(self.oos_equity),
        }


def walk_forward(
    df: pd.DataFrame,
    strategy_name: str,
    param_grid: dict[str, list],
    train_size: int,
    test_size: int,
    step: int | None = None,
    metric: str = "sharpe",
    backtest_kwargs: dict | None = None,
) -> WalkForwardResult:
    """Rolling walk-forward: optimise on each train window, score on the next
    test window, and stitch the out-of-sample results into one equity curve.

    Parameters
    ----------
    train_size, test_size : window lengths in bars.
    step                  : bars to advance each fold (defaults to test_size,
                            i.e. non-overlapping test segments).
    metric                : selection metric optimised on the train window.
    """
    backtest_kwargs = backtest_kwargs or {}
    step = step or test_size
    initial_cash = backtest_kwargs.get("initial_cash", 10_000.0)

    if train_size < 2 or test_size < 1:
        raise ValueError("train_size >= 2 and test_size >= 1 required")
    if len(df) < train_size + test_size:
        raise ValueError(
            f"need at least train_size+test_size ({train_size + test_size}) bars, "
            f"got {len(df)}"
        )

    folds: list[dict] = []
    oos_return_chunks: list[pd.Series] = []

    start = 0
    while start + train_size + test_size <= len(df):
        train = df.iloc[start : start + train_size]
        test = df.iloc[start + train_size : start + train_size + test_size]

        best_params, train_score = grid_search(
            train, strategy_name, param_grid, metric, backtest_kwargs
        )
        if best_params:
            strat = build_strategy(strategy_name, **best_params)
            signals = strat.generate_signals(test)
            test_result = run_backtest(test, signals, **backtest_kwargs)
            test_summary = performance_summary(test_result)
            oos_return_chunks.append(test_result.returns)
        else:
            best_params, train_score, test_summary = {}, float("nan"), {}
            oos_return_chunks.append(pd.Series(0.0, index=test.index))

        folds.append(
            {
                "train_start": train.index[0],
                "test_start": test.index[0],
                "test_end": test.index[-1],
                "best_params": best_params,
                "train_score": train_score,
                "oos_summary": test_summary,
            }
        )
        start += step

    oos_returns = pd.concat(oos_return_chunks) if oos_return_chunks else pd.Series(dtype=float)
    oos_returns = oos_returns[~oos_returns.index.duplicated(keep="first")]
    oos_equity = (1.0 + oos_returns).cumprod() * initial_cash
    oos_equity.name = "oos_equity"

    return WalkForwardResult(
        folds=folds,
        oos_returns=oos_returns,
        oos_equity=oos_equity,
        initial_cash=initial_cash,
    )
