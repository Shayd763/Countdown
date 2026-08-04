from .base import Strategy
from .ensemble import MultiFactorEnsemble
from .mean_reversion import MeanReversion
from .trend import TrendFollowing

REGISTRY = {
    "mean_reversion": MeanReversion,
    "trend": TrendFollowing,
    "ensemble": MultiFactorEnsemble,
}


def build_strategy(name: str, **params) -> Strategy:
    """Instantiate a registered strategy by config name."""
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](**params)


__all__ = [
    "Strategy", "MeanReversion", "TrendFollowing", "MultiFactorEnsemble",
    "REGISTRY", "build_strategy",
]
