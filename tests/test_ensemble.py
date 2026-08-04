import numpy as np
import pandas as pd
import pytest

from quantbot.strategies import MultiFactorEnsemble, build_strategy


def _frame(n=400, with_flows=True, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="1D", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0.1, 1.0, n))
    close = np.abs(close) + 1
    data = {"open": close, "high": close, "low": close, "close": close, "volume": 0.0}
    if with_flows:
        data["flow_in"] = np.abs(rng.normal(1000, 200, n))
        data["flow_out"] = np.abs(rng.normal(1000, 200, n))
    return pd.DataFrame(data, index=idx)


def test_registered():
    assert isinstance(build_strategy("ensemble"), MultiFactorEnsemble)


def test_exposure_is_fractional_and_bounded():
    df = _frame()
    sig = MultiFactorEnsemble(trend_ma=50).generate_signals(df)
    # two factors -> exposure in {0, 0.5, 1.0}
    assert set(np.unique(np.round(sig.to_numpy(), 6))) <= {0.0, 0.5, 1.0}
    assert sig.min() >= 0.0 and sig.max() <= 1.0
    assert sig.index.equals(df.index)


def test_trend_only_when_flows_disabled():
    df = _frame(with_flows=False)
    sig = MultiFactorEnsemble(trend_ma=50, use_flow=False).generate_signals(df)
    # single factor -> pure long/flat
    assert set(np.unique(sig.to_numpy())) <= {0.0, 1.0}


def test_missing_flow_columns_raises():
    df = _frame(with_flows=False)
    with pytest.raises(ValueError):
        MultiFactorEnsemble(trend_ma=50, use_flow=True).generate_signals(df)


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        MultiFactorEnsemble(trend_ma=1)
    with pytest.raises(ValueError):
        MultiFactorEnsemble(flow_long=2.0, flow_exit=1.0)


def test_warmup_is_flat():
    df = _frame()
    sig = MultiFactorEnsemble(trend_ma=50, flow_window=90).generate_signals(df)
    # before both factors have warmed up, exposure must be 0
    assert sig.iloc[:49].sum() == 0.0
