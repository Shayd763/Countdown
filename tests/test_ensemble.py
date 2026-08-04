import numpy as np
import pandas as pd
import pytest

from quantbot.strategies import MultiFactorEnsemble, build_strategy


def _frame(n=400, cols=("flow_in", "flow_out", "fee_ntv"), seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="1D", tz="UTC")
    close = np.abs(100 + np.cumsum(rng.normal(0.1, 1.0, n))) + 1
    data = {"open": close, "high": close, "low": close, "close": close, "volume": 0.0}
    for c in cols:
        data[c] = np.abs(rng.normal(1000, 200, n))
    return pd.DataFrame(data, index=idx)


def test_registered_with_default_factors():
    strat = build_strategy("ensemble")
    assert isinstance(strat, MultiFactorEnsemble)
    assert strat.factors == ("trend", "flow", "fee")


def test_three_factor_exposure_levels():
    df = _frame()
    sig = MultiFactorEnsemble(trend_ma=50).generate_signals(df)
    # three factors -> exposure in {0, 1/3, 2/3, 1}
    allowed = {0.0, round(1 / 3, 6), round(2 / 3, 6), 1.0}
    assert set(np.round(sig.to_numpy(), 6)) <= allowed
    assert sig.min() >= 0.0 and sig.max() <= 1.0
    assert sig.index.equals(df.index)


def test_two_factor_exposure_levels():
    df = _frame()
    sig = MultiFactorEnsemble(factors=("trend", "flow"), trend_ma=50).generate_signals(df)
    assert set(np.round(sig.to_numpy(), 6)) <= {0.0, 0.5, 1.0}


def test_trend_only_is_binary():
    df = _frame(cols=())
    sig = MultiFactorEnsemble(factors=("trend",), trend_ma=50).generate_signals(df)
    assert set(np.unique(sig.to_numpy())) <= {0.0, 1.0}


def test_missing_factor_columns_raises():
    df = _frame(cols=())  # no flow/fee columns
    with pytest.raises(ValueError):
        MultiFactorEnsemble(factors=("trend", "flow")).generate_signals(df)


def test_unknown_factor_rejected():
    with pytest.raises(ValueError):
        MultiFactorEnsemble(factors=("trend", "bogus"))


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        MultiFactorEnsemble(trend_ma=1)
    with pytest.raises(ValueError):
        MultiFactorEnsemble(flow_long=2.0, flow_exit=1.0)
    with pytest.raises(ValueError):
        MultiFactorEnsemble(fee_fast=400, fee_slow=100)


def test_warmup_is_flat():
    df = _frame()
    sig = MultiFactorEnsemble(trend_ma=50).generate_signals(df)
    assert sig.iloc[:49].sum() == 0.0
