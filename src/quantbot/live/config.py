"""Frozen live-trading configuration — the validated strategy, pinned.

These are the exact parameters validated in research (ensemble trend+flow+fee,
vol-target 0.35, drawdown de-risk 0.6, weekly rebalance) and confirmed on real
Kraken BTC/USD data (Sharpe ~1.29, max drawdown ~-23%). Changing them means
you are trading a *different*, unvalidated strategy — so they live in one frozen
place, separate from the operational knobs below.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StrategyConfig:
    # --- signal (validated; do not change without re-validating) ---
    factors: tuple[str, ...] = ("trend", "flow", "fee")
    trend_ma: int = 200
    flow_window: int = 90
    flow_long: float = -1.0
    flow_exit: float = 1.5
    fee_fast: int = 30
    fee_slow: int = 365
    # --- risk overlays (validated) ---
    target_vol: float = 0.35
    vol_window: int = 30
    dd_tolerance: float = 0.6
    quant: float = 1 / 3        # exposure quantised to thirds
    # --- rebalancing / execution policy ---
    rebalance_weekday: int = 0  # 0=Monday; only trade on this day...
    rebalance_band: float = 0.10  # ...and only if target vs held exposure differs by this
    # --- costs assumed in reconciliation math ---
    fee_bps: float = 26.0
    slippage_bps: float = 5.0


@dataclass(frozen=True)
class ExecutionConfig:
    """Operational + safety knobs (safe to tune; not part of the strategy)."""
    pair: str = "XXBTZUSD"          # Kraken BTC/USD; use "XXBTZGBP" for BTC/GBP
    quote_ccy: str = "USD"
    # Safety rails — deliberately conservative defaults.
    max_order_fraction: float = 0.34  # never move more than this fraction of equity in one order
    min_order_quote: float = 10.0     # skip dust trades below this notional
    max_stale_hours: float = 36.0     # abort if latest data bar is older than this
    killswitch_file: str = "KILLSWITCH"  # presence of this file halts all trading
    mode: str = "dry-run"           # "dry-run" | "paper" | "live"  (default = safest)
    state_file: str = ".live_state.json"
