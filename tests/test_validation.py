import numpy as np
import pandas as pd
import pytest

from quantbot.data import synthetic_ohlcv
from quantbot.validation import grid_search, walk_forward


def test_grid_search_returns_best_params():
    df = synthetic_ohlcv(n=500, timeframe="1d")
    grid = {"lookback": [20, 40], "entry_z": [1.5, 2.0], "exit_z": [0.5],
            "allow_short": [False]}
    params, score = grid_search(df, "mean_reversion", grid, metric="sharpe")
    assert set(params) == {"lookback", "entry_z", "exit_z", "allow_short"}
    assert params["lookback"] in (20, 40)
    assert np.isfinite(score)


def test_grid_search_skips_invalid_combos():
    df = synthetic_ohlcv(n=300, timeframe="1d")
    # entry_z must exceed exit_z; the 1.0/2.0 combo is invalid and must be skipped
    grid = {"lookback": [20], "entry_z": [1.0, 2.5], "exit_z": [2.0],
            "allow_short": [False]}
    params, score = grid_search(df, "mean_reversion", grid)
    assert params["entry_z"] == 2.5  # only the valid combo survives


def test_walk_forward_produces_oos_curve():
    df = synthetic_ohlcv(n=1000, timeframe="1d")
    grid = {"lookback": [20, 40], "entry_z": [1.5, 2.0], "exit_z": [0.5],
            "allow_short": [False]}
    result = walk_forward(df, "mean_reversion", grid,
                          train_size=200, test_size=50,
                          backtest_kwargs={"fee_bps": 26, "slippage_bps": 5})
    assert len(result.folds) > 0
    assert not result.oos_equity.empty
    s = result.summary
    assert "oos_total_return" in s and "oos_sharpe" in s
    # OOS segments must not overlap the training data of their own fold
    for fold in result.folds:
        assert fold["test_start"] > fold["train_start"]


def test_walk_forward_rejects_short_data():
    df = synthetic_ohlcv(n=100, timeframe="1d")
    with pytest.raises(ValueError):
        walk_forward(df, "mean_reversion", {"lookback": [20]},
                     train_size=200, test_size=50)


def test_oos_returns_have_no_duplicate_timestamps():
    df = synthetic_ohlcv(n=800, timeframe="1d")
    grid = {"lookback": [30], "entry_z": [2.0], "exit_z": [0.5], "allow_short": [False]}
    result = walk_forward(df, "mean_reversion", grid,
                          train_size=200, test_size=50)
    assert not result.oos_returns.index.duplicated().any()
