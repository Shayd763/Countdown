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

## What we've actually found (real data)

Tested on real Coin Metrics BTC/USD daily data (2016–2026), walk-forward,
after Kraken fees:

| Approach | OOS return | Sharpe | Max DD | Verdict |
|---|---|---|---|---|
| Short-horizon mean-reversion | −50% | −0.24 | −62% | ❌ rejected |
| Naive trend crossover (per-fold fit) | −13% | ~0.00 | −43% | ❌ rejected |
| Buy & hold BTC | +419%* | 0.63 | −82% | benchmark |
| **Trend-filter (long/flat vs slow MA)** | **+227%** | **0.72** | **−45%** | ✅ **survives** |

\*2018+ window. The lesson: trying to *predict* short-term moves loses money and
underperforms doing nothing. The edge that survives is **low-turnover trend
participation** — stay long BTC while it's above its slow moving average, sit in
cash when it isn't. It captures the trend, sidesteps the worst of the crashes,
and trades rarely enough to clear the cost hurdle. It is robust across MA
lengths (100–300), not a single lucky parameter.

**Caveats (important):** this period is largely one secular BTC bull market with
two big crashes — precisely the regime trend filters are built for; a long
sideways market would whipsaw it. Data is BTC/USD daily reference price (no
intraday, not GBP). Final validation needs venue-native Kraken OHLC before any
capital.

## Best strategy so far: multi-factor ensemble

No single retail signal has strong durable alpha — we tested and rejected
mean-reversion, single-asset momentum, cross-sectional crypto momentum, MVRV
valuation, and FX entirely. What holds up is **combining a few weak, independent
signals** (the way real systematic funds work). Two survived walk-forward:

- **trend** — price above its 200-day MA (regime / risk-on)
- **flow** — net exchange outflows (on-chain accumulation vs sell pressure)
- **fee** — total fees rising (blockspace-demand / network-usage momentum)

The `ensemble` strategy votes long/flat per factor and scales exposure to the
fraction agreeing. On real BTC (2016–2026, Kraken fees + 4.5% cash yield):

| | Sharpe | Max DD | OOS Sharpe |
|---|---|---|---|
| Buy & hold | 1.08 | −84% | 0.69 |
| Trend only | 1.15 | −65% | 0.48 |
| Flow only | 1.27 | −60% | 0.85 |
| Ensemble (trend+flow) | 1.34 | −57% | 0.76 |
| **Ensemble (trend+flow+fee)** | **1.37** | **−58%** | — |

Higher Sharpe than any single factor, roughly half the drawdown of buy-and-hold,
more stable out-of-sample. Adding `fee` lifted the weakest historical block.

**We are at diminishing returns:** marginal-contribution testing across time
blocks showed `hash` and `mvrv` actively *hurt*, `addr` was neutral, and a
kitchen-sink of all six factors was *worse* than three. So the default is
deliberately just three. More factors are not more edge.

### Full walk-forward (the honest expected number)

Re-selecting the factor set and trend length on each 2-year train window and
scoring on the next unseen 6 months (`scripts/ensemble_walkforward.py`):

| | Walk-forward OOS |
|---|---|
| Ensemble Sharpe | **0.97** |
| Buy & hold Sharpe | 0.69 |
| Ensemble CAGR / MaxDD | +38% / −57% |
| **Positive 6-mo folds** | **9 / 16** |

A real edge over buy-and-hold on risk-adjusted terms and drawdown — **but
violently volatile.** Nearly half of 6-month windows are negative and the
aggregate is carried by a few strong folds (per-fold Sharpe ranged −1.8 to
+4.1). This is a regime-dependent edge that demands stomach for long
underperformance, not a smooth outperformer. Size and expectations accordingly —
anyone showing you a smooth Sharpe-3 crypto curve is hiding this.

## Risk overlays: cut losses, frequency, and volume

The honest edge here is risk reduction, so the biggest wins come from managing
risk, not predicting direction. Two overlays (`quantbot.risk.overlay`) transform
any strategy's exposure:

- **volatility_target** — scale exposure down when realised vol is high (crashes
  cluster there). Cuts the left tail.
- **periodic_rebalance** — only change position every N bars (+ optional quantise).
  Cuts trade frequency and turnover.

Ensemble + weekly-rebalanced vol-target on BTC (2018+):

| | Sharpe | MaxDD | Trades/yr | Avg exposure |
|---|---|---|---|---|
| Buy & hold | 0.63 | −82% | 0.1 | 100% |
| Ensemble | 1.23 | −53% | 26 | 51% |
| **+ vol-target, weekly** | 1.19 | **−46%** | **15** | 44% |

Lower drawdown, ~half the trades, less capital at risk — at essentially unchanged
Sharpe. Monthly rebalancing is too slow (Sharpe drops to 0.80); weekly is the
sweet spot.

### Pushing loss-cutting further (the best risk-adjusted result)

Tightening the vol target and adding `drawdown_scale` (de-risk as the asset falls
from its 1-year peak) — BTC 2018+:

| Config | CAGR | Sharpe | MaxDD | Calmar | Trd/y |
|---|---|---|---|---|---|
| Buy & hold | +22% | 0.63 | −82% | 0.27 | 0.1 |
| Weekly vol-target 0.50 | +36% | 1.19 | −46% | 0.77 | 15 |
| Weekly vol-target 0.30 | +32% | 1.35 | −32% | 1.00 | 12 |
| **0.35 + drawdown de-risk** | **+26%** | **1.34** | **−18%** | **1.49** | 9 |

Max drawdown down to **−18%** — under a quarter of buy-and-hold's — while still
beating it on return, at ~9 trades/year. Calmar 1.49 vs 0.27. Both levers (tighter
vol target, drawdown-scaling) push the same way, so the effect is structural, not
a fragile fit — though the exact −18% is optimistic (parameters chosen in-sample).

**Asset note:** tested on ETH too — BTC won on every metric (ETH buy-hold −94%
drawdown vs BTC −82%; ensemble Sharpe 0.90 vs 1.23). ETH is *not* more profitable,
though vol-targeting helps it more because it's more volatile. BTC stays the base.

## Which asset class? (evidence-based)

Ran the identical trend-filter-vs-buy-&-hold test across four asset classes on
real data (monthly). Returns track the **asset's own risk premium first**,
strategy second:

| Asset (2015–26) | Buy&hold Sharpe | Trend helps? | Notes |
|---|---|---|---|
| Crypto (BTC) | 1.03 | ✅ | Highest return, brutal −66/−80% drawdowns |
| Equities (S&P) | 0.99 | risk only | Reliable premium, ISA tax-free, gentle DD |
| Gold | 0.95 | no | Low-return diversifier |
| **FX (GBP/USD)** | **0.18** | ❌ worse | **~No risk premium — zero-sum. Worst base for a directional bot.** |

The FX result is decisive: a currency pair has no secular drift to harvest, so
buy-and-hold earns ~1%/yr and trend-following can't manufacture an edge. FX only
pays via pure-alpha games (carry, market-making) that retail loses structurally.
Cheaper fees don't fix a market you're built to lose — and higher frequency
*multiplies* cost drag rather than reducing it.

## Chosen focus: equities trend filter in a UK ISA

The lowest-stress, tax-efficient path. Validated on **S&P 500 total return**
(long history incl. 2000 & 2008), 10-month trend filter, idle cash at 4.5%:

| 1990–2026 | CAGR | Sharpe | Max DD |
|---|---|---|---|
| Buy & hold | +10.7% | 0.90 | −49% |
| **Trend filter (+cash yield)** | **+10.7%** | **1.21** | **−19%** |

Same return, much higher Sharpe, **less than half the drawdown** — and inside a
Stocks & Shares ISA, entirely tax-free with no leverage. See
`config/equities.example.yaml`. Check today's position with
`scripts/signal_today.py`. It's low-frequency (a few trades a year), so it's
systematic discipline more than an execution "bot".

## Beating costs (the whole game)

Two mechanisms exist specifically to make "profitable after fees" honest:

* **Cost gate** — the strategy only enters when its *expected* move clears
  round-trip cost by a safety multiple (`cost_bps`, `edge_safety`). No more
  trading into a 0.5% hurdle for a 0.3% expected move.
* **Walk-forward validation** (`scripts/run_walkforward.py`) — parameters are
  chosen on each train window and scored only on the *next unseen* test window,
  costs included. The stitched out-of-sample curve is the verdict. On the
  synthetic fixture with Kraken fees, mean-reversion is **rejected** — as it
  should be. That is the kill-fast loop working, not a bug.

## Roadmap

- [x] **1. Data & infra** — ingestion, caching, reproducible offline fixture
- [x] **2. Backtesting engine** — honest fills, fees, slippage, no lookahead
- [x] **3. Strategy research** — mean-reversion v1 + metrics (Sharpe, DD, win rate)
- [x] **4. Risk layer** — fixed-fractional & fractional-Kelly sizing
- [x] **5. Validation** — cost gate + walk-forward out-of-sample testing
- [ ] **6. Real data** — Kraken BTC/GBP history; find an edge that survives OOS
- [ ] **7. Paper trading** — live data, fake money, backtest-vs-reality gap check
- [ ] **8. Live (small)** — real money, tiny size, monitoring, kill-switch
- [ ] **9. Ops** — logging, dashboards, health checks, secret management

We now have a validation loop that rejects marginal strategies cheaply. Next:
run it on real Kraken data and search for an edge that actually survives it.

## Disclaimer

This software is for research and education. It is **not** financial advice.
Trading carries substantial risk of loss. Never risk money you can't afford to
lose, and validate thoroughly on out-of-sample data and paper trading before
going live.
