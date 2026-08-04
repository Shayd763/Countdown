import numpy as np
import pandas as pd

from quantbot.strategies import TrendFollowing, build_strategy


def test_registered_in_registry():
    assert isinstance(build_strategy("trend", fast=10, slow=30), TrendFollowing)


def test_invalid_params_rejected():
    import pytest

    with pytest.raises(ValueError):
        TrendFollowing(fast=50, slow=20)  # fast must be shorter than slow
    with pytest.raises(ValueError):
        TrendFollowing(fast=10, slow=30, band=-0.1)


def test_signals_valid_and_flat_during_warmup():
    idx = pd.date_range("2021-01-01", periods=200, freq="1D", tz="UTC")
    close = np.linspace(100, 200, 200)
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
        index=idx,
    )
    strat = TrendFollowing(fast=10, slow=50, allow_short=False)
    sig = strat.generate_signals(df)
    assert set(np.unique(sig.to_numpy())) <= {-1.0, 0.0, 1.0}
    assert (sig.iloc[:49] == 0.0).all()  # slow-MA(50) valid at index 49 -> flat before


def test_goes_long_in_uptrend():
    idx = pd.date_range("2021-01-01", periods=200, freq="1D", tz="UTC")
    close = np.linspace(100, 300, 200)  # steady uptrend
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
        index=idx,
    )
    strat = TrendFollowing(fast=10, slow=50, allow_short=False)
    sig = strat.generate_signals(df)
    assert (sig.iloc[60:] == 1.0).all()  # fast MA above slow throughout the trend


def test_long_only_never_shorts_in_downtrend():
    idx = pd.date_range("2021-01-01", periods=200, freq="1D", tz="UTC")
    close = np.linspace(300, 100, 200)  # steady downtrend
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
        index=idx,
    )
    strat = TrendFollowing(fast=10, slow=50, allow_short=False)
    sig = strat.generate_signals(df)
    assert (sig >= 0).all()  # downtrend -> flat, never short


def test_band_reduces_turnover():
    # Choppy market: a band should produce fewer position changes than none.
    rng = np.random.default_rng(0)
    idx = pd.date_range("2021-01-01", periods=400, freq="1D", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 1, 400))
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
        index=idx,
    )
    no_band = TrendFollowing(fast=10, slow=30, band=0.0).generate_signals(df)
    with_band = TrendFollowing(fast=10, slow=30, band=0.03).generate_signals(df)
    flips_none = (no_band.diff().fillna(0) != 0).sum()
    flips_band = (with_band.diff().fillna(0) != 0).sum()
    assert flips_band <= flips_none
