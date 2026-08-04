import numpy as np
import pandas as pd
import pytest

from quantbot.data import synthetic_ohlcv
from quantbot.strategies import MeanReversion, build_strategy


def test_registry_builds_strategy():
    strat = build_strategy("mean_reversion", lookback=10, entry_z=2.0, exit_z=0.5)
    assert isinstance(strat, MeanReversion)


def test_unknown_strategy_raises():
    with pytest.raises(KeyError):
        build_strategy("does_not_exist")


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        MeanReversion(lookback=1)
    with pytest.raises(ValueError):
        MeanReversion(entry_z=0.5, exit_z=2.0)  # entry must exceed exit


def test_signals_are_valid_and_no_lookahead():
    df = synthetic_ohlcv(n=400)
    strat = MeanReversion(lookback=48, entry_z=2.0, exit_z=0.5)
    sig = strat.generate_signals(df)
    # values in {-1,0,1}
    assert set(np.unique(sig.to_numpy())) <= {-1.0, 0.0, 1.0}
    # first `lookback` bars have no z-score -> must be flat
    assert (sig.iloc[:48] == 0.0).all()
    # aligned to index
    assert sig.index.equals(df.index)


def test_long_only_never_shorts():
    df = synthetic_ohlcv(n=400)
    strat = MeanReversion(lookback=48, entry_z=1.5, exit_z=0.5, allow_short=False)
    sig = strat.generate_signals(df)
    assert (sig >= 0).all()


def test_enters_long_when_price_dips():
    # Construct a clean dip: flat, then a sharp drop below the mean band.
    n = 80
    idx = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    close = np.full(n, 100.0)
    close[60:] = 90.0  # sharp drop -> very negative z-score -> go long
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
        index=idx,
    )
    strat = MeanReversion(lookback=20, entry_z=2.0, exit_z=0.5, allow_short=False)
    sig = strat.generate_signals(df)
    assert (sig.iloc[60:] == 1.0).any()
