from .base import Strategy
from .mean_reversion import MeanReversion

REGISTRY = {
    "mean_reversion": MeanReversion,
}


def build_strategy(name: str, **params) -> Strategy:
    """Instantiate a registered strategy by config name."""
    if name not in REGISTRY:
        raise KeyError(f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](**params)


__all__ = ["Strategy", "MeanReversion", "REGISTRY", "build_strategy"]
