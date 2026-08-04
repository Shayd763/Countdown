"""Brokers — turn a target exposure into orders, safely.

Three implementations behind one interface:
  DryRunBroker  logs what it *would* do, holds no money. The default.
  PaperBroker   simulates fills against live prices, tracks a fake portfolio.
  KrakenBroker  places real orders via the Kraken API (keys from env). Gated.

All order sizing goes through `plan_order`, which applies the safety rails
(max order fraction, min notional) so every path shares the same guards.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .config import ExecutionConfig


@dataclass
class Portfolio:
    quote: float          # cash in quote currency (USD/GBP)
    base: float           # units of BTC held
    price: float          # current BTC price in quote

    @property
    def equity(self) -> float:
        return self.quote + self.base * self.price

    @property
    def exposure(self) -> float:
        return (self.base * self.price) / self.equity if self.equity > 0 else 0.0


@dataclass
class OrderPlan:
    side: str             # "buy" | "sell" | "none"
    base_units: float     # BTC to trade (absolute)
    quote_value: float    # notional in quote ccy
    reason: str


def plan_order(pf: Portfolio, target_exposure: float, cfg: ExecutionConfig) -> OrderPlan:
    """Compute the order to move from current to target exposure, with safety caps."""
    target_base_value = target_exposure * pf.equity
    current_base_value = pf.base * pf.price
    delta_value = target_base_value - current_base_value

    # Safety cap: never move more than max_order_fraction of equity in one order.
    cap = cfg.max_order_fraction * pf.equity
    if abs(delta_value) > cap:
        delta_value = cap if delta_value > 0 else -cap

    if abs(delta_value) < cfg.min_order_quote:
        return OrderPlan("none", 0.0, 0.0, f"below min notional ({abs(delta_value):.2f})")
    if pf.price <= 0:
        return OrderPlan("none", 0.0, 0.0, "no valid price")

    side = "buy" if delta_value > 0 else "sell"
    units = abs(delta_value) / pf.price
    return OrderPlan(side, units, abs(delta_value), f"{side} {units:.6f} BTC (~{abs(delta_value):.0f})")


class Broker(ABC):
    @abstractmethod
    def get_portfolio(self, price: float) -> Portfolio: ...
    @abstractmethod
    def execute(self, plan: OrderPlan) -> str: ...


class DryRunBroker(Broker):
    """Logs intended orders; touches nothing. Uses a nominal portfolio for math."""
    def __init__(self, quote: float = 1000.0, base: float = 0.0):
        self._quote, self._base = quote, base

    def get_portfolio(self, price: float) -> Portfolio:
        return Portfolio(self._quote, self._base, price)

    def execute(self, plan: OrderPlan) -> str:
        return f"DRY-RUN: would {plan.reason}" if plan.side != "none" else "DRY-RUN: no trade"


class PaperBroker(DryRunBroker):
    """Simulates fills against the live price so you can track a fake track record."""
    def execute(self, plan: OrderPlan) -> str:
        if plan.side == "none":
            return "PAPER: no trade"
        signed = plan.base_units if plan.side == "buy" else -plan.base_units
        # naive fill at price; a spread/slippage model can be added here later
        price = plan.quote_value / plan.base_units
        self._base += signed
        self._quote -= signed * price
        return f"PAPER: filled {plan.reason}"


class KrakenBroker(Broker):
    """Real trading via Kraken. Requires EXCHANGE_API_KEY / EXCHANGE_API_SECRET.

    Uses ccxt if available. This is the only path that moves real money — it is
    never selected unless mode='live' is set explicitly by the operator.
    """
    def __init__(self, cfg: ExecutionConfig):
        self.cfg = cfg
        key, secret = os.environ.get("EXCHANGE_API_KEY"), os.environ.get("EXCHANGE_API_SECRET")
        if not key or not secret:
            raise RuntimeError("live mode needs EXCHANGE_API_KEY and EXCHANGE_API_SECRET env vars")
        try:
            import ccxt
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("live mode needs ccxt (`pip install ccxt`)") from e
        self._ex = ccxt.kraken({"apiKey": key, "secret": secret, "enableRateLimit": True})

    def get_portfolio(self, price: float) -> Portfolio:  # pragma: no cover - needs network
        bal = self._ex.fetch_balance()
        return Portfolio(float(bal["total"].get(self.cfg.quote_ccy, 0.0)),
                         float(bal["total"].get("BTC", 0.0)), price)

    def execute(self, plan: OrderPlan) -> str:  # pragma: no cover - needs network
        if plan.side == "none":
            return "LIVE: no trade"
        order = self._ex.create_order("BTC/" + self.cfg.quote_ccy, "market", plan.side, plan.base_units)
        return f"LIVE: placed {plan.side} {plan.base_units:.6f} BTC (id {order.get('id')})"


def make_broker(cfg: ExecutionConfig) -> Broker:
    """Select the broker for the configured mode. Defaults to the safest."""
    if cfg.mode == "live":
        return KrakenBroker(cfg)
    if cfg.mode == "paper":
        return PaperBroker()
    return DryRunBroker()
