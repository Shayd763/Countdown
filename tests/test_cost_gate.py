import numpy as np
import pandas as pd

from quantbot.strategies import MeanReversion


def _small_dip_market(n=80, price=100.0, dip=99.0):
    """A market whose deviations are tiny relative to price, so the expected
    reversion move is smaller than any realistic round-trip cost."""
    idx = pd.date_range("2023-01-01", periods=n, freq="1D", tz="UTC")
    close = np.full(n, price)
    close[60:] = dip  # ~1% dip
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
        index=idx,
    )


def test_gate_blocks_trades_below_cost_hurdle():
    df = _small_dip_market()
    # Huge cost gate (10% round trip) with safety 1.5 -> nothing should trade.
    strat = MeanReversion(lookback=20, entry_z=1.5, exit_z=0.5,
                          allow_short=False, cost_bps=1000, edge_safety=1.5)
    sig = strat.generate_signals(df)
    assert (sig == 0.0).all()


def test_gate_allows_trades_above_cost_hurdle():
    df = _small_dip_market()
    # Tiny cost gate (2 bps) -> the ~1% expected move clears it easily.
    strat = MeanReversion(lookback=20, entry_z=1.5, exit_z=0.5,
                          allow_short=False, cost_bps=2, edge_safety=1.0)
    sig = strat.generate_signals(df)
    assert (sig == 1.0).any()


def test_zero_cost_bps_disables_gate():
    df = _small_dip_market()
    ungated = MeanReversion(lookback=20, entry_z=1.5, exit_z=0.5,
                            allow_short=False, cost_bps=0.0)
    sig = ungated.generate_signals(df)
    assert (sig == 1.0).any()
