import numpy as np
import pandas as pd
import pytest

from quantbot.risk import periodic_rebalance, realized_vol, volatility_target


def _returns(vol, n=100, seed=0):
    idx = pd.date_range("2021-01-01", periods=n, freq="1D", tz="UTC")
    return pd.Series(np.random.default_rng(seed).normal(0, vol, n), index=idx)


def test_realized_vol_annualises():
    r = _returns(0.02, n=400)          # ~2% daily -> ~38% annualised
    rv = realized_vol(r, window=30).dropna()
    assert 0.25 < rv.mean() < 0.55


def test_vol_target_shrinks_in_high_vol():
    calm = _returns(0.01, n=200, seed=1)
    wild = _returns(0.06, n=200, seed=2)
    expo = pd.Series(1.0, index=calm.index)
    calm_scaled = volatility_target(expo, calm, target_vol=0.30).iloc[40:].mean()
    wild_scaled = volatility_target(expo, wild.set_axis(calm.index), target_vol=0.30).iloc[40:].mean()
    assert wild_scaled < calm_scaled  # higher vol -> smaller position


def test_vol_target_never_levers_beyond_cap():
    r = _returns(0.001, n=200)          # very calm -> scalar would blow up
    expo = pd.Series(1.0, index=r.index)
    scaled = volatility_target(expo, r, target_vol=0.50, max_leverage=1.0)
    assert scaled.max() <= 1.0 + 1e-9


def test_vol_target_rejects_bad_target():
    r = _returns(0.02)
    with pytest.raises(ValueError):
        volatility_target(pd.Series(1.0, index=r.index), r, target_vol=0.0)


def test_periodic_rebalance_reduces_changes():
    idx = pd.date_range("2021-01-01", periods=100, freq="1D", tz="UTC")
    noisy = pd.Series(np.random.default_rng(0).uniform(0, 1, 100), index=idx)
    daily_changes = (noisy.diff().fillna(0) != 0).sum()
    weekly = periodic_rebalance(noisy, every=7)
    weekly_changes = (weekly.diff().fillna(0) != 0).sum()
    assert weekly_changes < daily_changes


def test_periodic_rebalance_quantises():
    idx = pd.date_range("2021-01-01", periods=20, freq="1D", tz="UTC")
    expo = pd.Series(np.linspace(0, 1, 20), index=idx)
    q = periodic_rebalance(expo, every=1, quant=0.25)
    assert set(np.unique(np.round(q.to_numpy(), 6))) <= {0.0, 0.25, 0.5, 0.75, 1.0}


def test_periodic_rebalance_every_one_noop():
    idx = pd.date_range("2021-01-01", periods=10, freq="1D", tz="UTC")
    expo = pd.Series(np.linspace(0, 1, 10), index=idx)
    pd.testing.assert_series_equal(periodic_rebalance(expo, every=1), expo)
