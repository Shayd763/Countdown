import numpy as np
import pandas as pd
import pytest

from quantbot.live import (
    ExecutionConfig,
    Portfolio,
    StrategyConfig,
    decide,
    make_broker,
    plan_order,
    target_exposure_series,
)


def _frame(n=500, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="1D", tz="UTC")
    close = np.abs(100 + np.cumsum(rng.normal(0.1, 1.0, n))) + 1
    return pd.DataFrame({
        "open": close, "high": close, "low": close, "close": close, "volume": 0.0,
        "flow_in": np.abs(rng.normal(1000, 200, n)),
        "flow_out": np.abs(rng.normal(1000, 200, n)),
        "fee_ntv": np.abs(rng.normal(50, 10, n)),
    }, index=idx)


def test_target_exposure_bounded_and_quantised():
    expo = target_exposure_series(_frame(), StrategyConfig())
    assert expo.min() >= 0.0 and expo.max() <= 1.0
    # quantised to thirds (times the vote fractions) -> limited distinct levels
    assert expo.nunique() <= 12


def test_decide_only_trades_on_rebalance_weekday():
    df = _frame()
    cfg = StrategyConfig(rebalance_weekday=0)
    # force a non-Monday last bar
    while df.index[-1].weekday() == 0:
        df = df.iloc[:-1]
    d = decide(df, held_exposure=0.0, cfg=cfg)
    assert d.should_trade is False and "not a rebalance day" in d.reason


def test_decide_respects_rebalance_band():
    df = _frame()
    # make last bar a Monday
    while df.index[-1].weekday() != 0:
        df = df.iloc[:-1]
    cfg = StrategyConfig(rebalance_weekday=0, rebalance_band=0.10)
    target = float(target_exposure_series(df, cfg).iloc[-1])
    # held == target -> no trade (within band)
    assert decide(df, held_exposure=target, cfg=cfg).should_trade is False


def test_plan_order_buy_and_sell_direction():
    cfg = ExecutionConfig()
    pf = Portfolio(quote=1000.0, base=0.0, price=100.0)   # all cash
    buy = plan_order(pf, target_exposure=0.5, cfg=cfg)
    assert buy.side == "buy" and buy.base_units > 0
    pf2 = Portfolio(quote=0.0, base=10.0, price=100.0)    # all BTC
    sell = plan_order(pf2, target_exposure=0.0, cfg=cfg)
    assert sell.side == "sell"


def test_plan_order_respects_max_fraction_cap():
    cfg = ExecutionConfig(max_order_fraction=0.1)
    pf = Portfolio(quote=1000.0, base=0.0, price=100.0)   # equity 1000
    plan = plan_order(pf, target_exposure=1.0, cfg=cfg)   # wants to deploy all 1000
    assert plan.quote_value <= 0.1 * pf.equity + 1e-9     # capped at 10%


def test_plan_order_skips_dust():
    cfg = ExecutionConfig(min_order_quote=10.0)
    pf = Portfolio(quote=1000.0, base=0.0, price=100.0)
    plan = plan_order(pf, target_exposure=0.005, cfg=cfg)  # ~5 notional < 10
    assert plan.side == "none"


def test_default_broker_is_dry_run():
    broker = make_broker(ExecutionConfig())  # default mode
    plan = plan_order(Portfolio(1000, 0, 100), 0.5, ExecutionConfig())
    assert "DRY-RUN" in broker.execute(plan)


def test_live_mode_without_keys_raises(monkeypatch):
    monkeypatch.delenv("EXCHANGE_API_KEY", raising=False)
    monkeypatch.delenv("EXCHANGE_API_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        make_broker(ExecutionConfig(mode="live"))
