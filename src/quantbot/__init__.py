"""quantbot — a config-driven crypto trading research & execution framework.

Layers:
    data       market data ingestion (ccxt + synthetic fallback)
    strategies pluggable signal generators (no lookahead by contract)
    backtest   honest event-simulated fills with fees & slippage + metrics
    risk       position sizing (fixed-fractional, fractional Kelly)
"""

__version__ = "0.1.0"
