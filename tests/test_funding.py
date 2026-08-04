import numpy as np
import pandas as pd
import pytest

from quantbot.funding import funding_summary, run_funding_arb


def _funding(vals):
    idx = pd.date_range("2021-01-01", periods=len(vals), freq="8h", tz="UTC")
    return pd.Series(vals, index=idx, dtype=float)


def test_always_collects_positive_funding():
    f = _funding([0.001] * 50)          # steady positive funding
    res = run_funding_arb(f, mode="always", per_leg_bps=0.0)
    assert res.final_equity > res.initial
    # one entry only (0->1 at start), no further switches
    assert res.switches == 1


def test_always_pays_negative_funding():
    f = _funding([-0.001] * 50)         # persistent negative funding -> loses
    res = run_funding_arb(f, mode="always", per_leg_bps=0.0)
    assert res.final_equity < res.initial


def test_conditional_steps_out_when_negative():
    f = _funding([0.001] * 20 + [-0.001] * 20)
    res = run_funding_arb(f, mode="conditional", entry=0.0, exit=-1e-4, per_leg_bps=0.0)
    # should be flat during the negative stretch
    assert res.position.iloc[-1] == 0.0
    assert res.switches >= 2


def test_costs_reduce_return():
    f = _funding([0.0005] * 100)
    cheap = run_funding_arb(f, mode="always", per_leg_bps=0.0).final_equity
    dear = run_funding_arb(f, mode="always", per_leg_bps=20.0).final_equity
    assert dear < cheap


def test_conditional_requires_entry_above_exit():
    f = _funding([0.001] * 10)
    with pytest.raises(ValueError):
        run_funding_arb(f, mode="conditional", entry=-0.001, exit=0.001)


def test_summary_keys():
    f = _funding(list(np.random.default_rng(0).normal(0.0001, 0.0003, 300)))
    m = funding_summary(run_funding_arb(f, mode="always"))
    for k in ("total_return", "cagr", "sharpe", "max_drawdown",
              "avg_funding_annualized", "pct_intervals_positive", "switches"):
        assert k in m
