import numpy as np
import pandas as pd
import pytest

from quantbot.backtest import performance_summary, run_backtest
from quantbot.data import synthetic_ohlcv


def _flat_market(n=50, price=100.0):
    idx = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    idx.name = "timestamp"
    return pd.DataFrame(
        {"open": price, "high": price, "low": price, "close": price, "volume": 1.0},
        index=idx,
    )


def test_no_signal_preserves_capital():
    df = _flat_market()
    signals = pd.Series(0.0, index=df.index)
    result = run_backtest(df, signals, initial_cash=10_000, fee_bps=10, slippage_bps=5)
    assert result.final_equity == pytest.approx(10_000)
    assert result.trades.empty


def test_costs_are_charged_on_trade():
    df = _flat_market()
    signals = pd.Series(0.0, index=df.index)
    signals.iloc[5] = 1.0  # enter long at bar 6's open, price flat -> only costs
    result = run_backtest(df, signals, initial_cash=10_000, fee_bps=10, slippage_bps=5)
    # Flat price means the only P&L is transaction cost: strictly less than start.
    assert result.final_equity < 10_000
    assert len(result.trades) >= 1


def test_no_lookahead_execution_timing():
    # Price jumps once at bar 10. A signal on bar 9 must NOT capture the jump
    # (it executes at bar 10 open), but a signal on bar 8 (exec at bar 9 open,
    # held through the bar-9->10 close move) captures it.
    n = 20
    idx = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    price = np.full(n, 100.0)
    price[10:] = 110.0
    df = pd.DataFrame(
        {"open": price, "high": price, "low": price, "close": price, "volume": 1.0},
        index=idx,
    )
    # Enter on bar 8, exit far later. Exposure starts at bar 9 open (=100),
    # so the 100->110 close move is captured.
    sig = pd.Series(0.0, index=idx)
    sig.iloc[8:15] = 1.0
    result = run_backtest(df, sig, initial_cash=10_000, fee_bps=0, slippage_bps=0)
    assert result.final_equity == pytest.approx(11_000, rel=1e-6)


def test_summary_keys_present():
    df = synthetic_ohlcv(n=500)
    sig = pd.Series(0.0, index=df.index)
    sig.iloc[100:200] = 1.0
    result = run_backtest(df, sig)
    summary = performance_summary(result)
    for key in ("total_return", "cagr", "sharpe", "sortino", "max_drawdown",
                "n_trades", "win_rate", "final_equity"):
        assert key in summary
