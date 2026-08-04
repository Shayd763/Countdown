"""Funding-rate arbitrage (delta-neutral cash-and-carry).

Hold spot BTC long and short an equal-notional perpetual future. Net exposure to
the BTC price is ~zero, so the P&L is the *funding* the short perp collects (paid
by longs to shorts when funding is positive) minus trading costs. It is a
structural carry edge, not a directional bet — typically high Sharpe with the
main risk being extended negative-funding (bear-market) stretches.

Requires derivatives access (perps) — available to EU/US institutional accounts,
not UK retail.
"""

from .backtest import FundingResult, run_funding_arb, funding_summary

__all__ = ["run_funding_arb", "FundingResult", "funding_summary"]
