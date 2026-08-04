"""Position sizing.

Two schemes, both returning a *fraction of equity* to allocate to a position
(0..max_position). Sizing is deliberately separate from signal generation:
"when to trade" and "how much to trade" are different questions, and mixing
them is how bots blow up.

  fixed_fractional   Risk a fixed % of equity per trade, derived from the stop
                     distance. The classic, robust default.

  fractional_kelly   Scale the theoretically growth-optimal Kelly fraction down
                     by a factor (e.g. 0.25 = quarter-Kelly). Full Kelly is far
                     too aggressive in practice — drawdowns are brutal and the
                     edge estimate is always noisy — so we always fraction it.
"""

from __future__ import annotations


def fixed_fractional(
    equity: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade: float = 0.01,
    max_position: float = 1.0,
) -> float:
    """Fraction of equity to allocate so that hitting the stop loses
    ``risk_per_trade`` of equity.

    Returns a value in [0, max_position]. Requires entry != stop.
    """
    if equity <= 0:
        return 0.0
    stop_distance = abs(entry_price - stop_price)
    if stop_distance == 0 or entry_price <= 0:
        return 0.0
    # Loss fraction if price moves from entry to stop, per unit of notional.
    loss_per_notional = stop_distance / entry_price
    dollars_at_risk = risk_per_trade * equity
    notional = dollars_at_risk / loss_per_notional
    fraction = notional / equity
    return max(0.0, min(fraction, max_position))


def kelly_fraction(win_prob: float, win_loss_ratio: float) -> float:
    """Full-Kelly fraction for a bet with the given edge.

    win_prob        probability of a winning trade, in (0, 1)
    win_loss_ratio  average win size / average loss size (b), > 0

    f* = p - (1 - p) / b. Clamped at 0 (never bet a negative edge).
    """
    if not 0.0 < win_prob < 1.0 or win_loss_ratio <= 0:
        return 0.0
    f = win_prob - (1.0 - win_prob) / win_loss_ratio
    return max(0.0, f)


def fractional_kelly(
    win_prob: float,
    win_loss_ratio: float,
    kelly_frac: float = 0.25,
    max_position: float = 1.0,
) -> float:
    """Down-scaled Kelly fraction, clamped to [0, max_position]."""
    f = kelly_fraction(win_prob, win_loss_ratio) * kelly_frac
    return max(0.0, min(f, max_position))
