"""Live trading pipeline: fresh data -> today's target exposure -> safe execution.

Safe by default (dry-run). Real orders require mode='live' plus API keys in the
environment. Always paper-trade first — historical validation is not live proof.
"""

from .broker import Broker, DryRunBroker, KrakenBroker, OrderPlan, PaperBroker, Portfolio, make_broker, plan_order
from .config import ExecutionConfig, StrategyConfig
from .pipeline import Decision, decide, target_exposure_series

__all__ = [
    "StrategyConfig", "ExecutionConfig",
    "target_exposure_series", "decide", "Decision",
    "Broker", "DryRunBroker", "PaperBroker", "KrakenBroker",
    "Portfolio", "OrderPlan", "plan_order", "make_broker",
]
