# quantbot

A config-driven framework for researching, backtesting, and (eventually)
deploying systematic crypto trading strategies — built **honesty-first**.

> **Reality check.** Most retail trading bots lose money. Fees, slippage, and
> overfitting quietly kill strategies that look great on paper. This project is
> deliberately built to *disprove* strategies cheaply: realistic costs, no
> lookahead bias, and risk controls that come before any promise of return.
> Backtested and synthetic results are **not** live performance.

## Thesis

The bot's edge is **discipline and cost-efficiency**, not prediction genius.
We hunt for small, persistent, structural inefficiencies (here: short-horizon
**mean reversion**), exploit them with strict risk controls and low transaction
costs, and let them compound. If a strategy only survives with zero costs or
same-bar execution, it does not survive.

**Asset class:** crypto (spot/perp). No $25k pattern-day-trader minimum, 24/7
markets, free granular data, excellent APIs. The framework ports to equities
(Alpaca) later without architectural change.

## Architecture

```
src/quantbot/
  data/        market-data ingestion (ccxt live + synthetic offline fixture)
  strategies/  pluggable signal generators; no-lookahead by contract
  backtest/    event-simulated fills with fees & slippage, + performance metrics
  risk/        position sizing (fixed-fractional, fractional Kelly)
```

Each layer is independent and swappable via config. Signals answer *when* to
trade; the risk layer answers *how much*; the engine answers *what it costs*.

### No-lookahead guarantee

A strategy emits a target position for bar `t` using only data up to `t`. The
backtest engine executes the change at the **open of bar `t+1`** and charges
fees + slippage on every position change. A signal can never trade on its own
bar's information.

## Quickstart

```bash
pip install -r requirements.txt

# Offline: runs on a synthetic mean-reverting series, no network/keys needed
python scripts/run_backtest.py --synthetic

# Real data + strategy from config
cp config/config.example.yaml config/config.yaml   # then edit
python scripts/run_backtest.py --config config/config.yaml

# Tests (all run offline)
python -m pytest tests/ -q
```

## Configuration

Everything is driven by `config/config.yaml` (gitignored — never commit keys).
See `config/config.example.yaml` for the full annotated schema covering the
data source, strategy params, risk method, and backtest costs. Live/paper API
keys are read from environment variables (`EXCHANGE_API_KEY` /
`EXCHANGE_API_SECRET`), never from the config file.

## Roadmap

- [x] **1. Data & infra** — ingestion, caching, reproducible offline fixture
- [x] **2. Backtesting engine** — honest fills, fees, slippage, no lookahead
- [x] **3. Strategy research** — mean-reversion v1 + metrics (Sharpe, DD, win rate)
- [x] **4. Risk layer** — fixed-fractional & fractional-Kelly sizing
- [ ] **5. Paper trading** — live data, fake money, backtest-vs-reality gap check
- [ ] **6. Live (small)** — real money, tiny size, monitoring, kill-switch
- [ ] **7. Ops** — logging, dashboards, health checks, secret management

We are at the end of phase 4: a validated research loop. Next up is walk-forward
/ out-of-sample validation and a paper-trading harness — **before** any capital.

## Disclaimer

This software is for research and education. It is **not** financial advice.
Trading carries substantial risk of loss. Never risk money you can't afford to
lose, and validate thoroughly on out-of-sample data and paper trading before
going live.
