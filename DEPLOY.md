# Deploying the live bot

This runs the validated strategy (BTC ensemble + risk overlays) as a daily,
hands-off loop. It is **safe by default** — dry-run, no orders — and only moves
real money when you explicitly set live mode with your own API keys.

> **Read this first.** The strategy is validated on history and on real Kraken
> data, but that is **not** proof of future profit. It is regime-dependent (it
> earns its keep in bear markets and lags in bulls), roughly half of 6-month
> stretches are negative, and the honest max drawdown is ~**−23%**. Deploy the
> way professionals do: **paper first, then tiny, then scale — never straight to
> full size.** Only risk money you can afford to lose.

## 1. Prerequisites

- Python 3.11+, `pip install -r requirements.txt` (plus `ccxt` for live trading)
- A Kraken account (UK-legal, spot only — no leverage/derivatives)
- The deep-history CSV `XBTUSD_1440.csv` from Kraken (warms up the 200-day signal)

## 2. The paper-first rollout (do these in order)

```bash
# Stage 1 — DRY RUN: see the signal, trade nothing. Run daily for a week.
python scripts/run_live.py --mode dry-run --kraken-csv XBTUSD_1440.csv

# Stage 2 — PAPER: simulate fills, build a real-time track record. Run for weeks.
python scripts/run_live.py --mode paper --kraken-csv XBTUSD_1440.csv

# Stage 3 — LIVE, TINY: real orders with money you can lose. Keys required.
export EXCHANGE_API_KEY=...        # Kraken key with *trade* permission only
export EXCHANGE_API_SECRET=...     # never withdrawal permission
python scripts/run_live.py --mode live --kraken-csv XBTUSD_1440.csv

# Stage 4 — scale up only after paper and tiny-live match the backtest's behaviour.
```

Compare each stage to the last before advancing. If paper doesn't behave like the
backtest, stop and investigate — don't proceed to live.

## 3. Schedule it (once per day)

The strategy is daily with a weekly rebalance, so one run per day is plenty. Cron
at 00:05 UTC:

```cron
5 0 * * * cd /path/to/Countdown && /usr/bin/python3 scripts/run_live.py \
  --mode paper --kraken-csv XBTUSD_1440.csv >> live.log 2>&1
```

Each run prints one JSON line (date, price, target exposure, whether it traded,
result) — easy to grep, alert on, or ship to a dashboard.

## 4. Safety rails (built in)

- **Kill-switch:** `touch KILLSWITCH` in the working dir halts all trading instantly.
- **Staleness guard:** refuses to trade if the latest data bar is >36h old.
- **Order caps:** never moves more than `max_order_fraction` (34%) of equity per
  order; skips dust below `min_order_quote`.
- **Data failure = no trade:** any fetch error logs and exits without trading.
- **Keys:** use a Kraken API key with **trade-only** permission — never enable
  withdrawals on the key the bot holds.

## 5. What to watch

- The daily `target exposure` (0, ⅓, ⅔, 1) and whether reality matches it.
- Drawdown vs the ~−23% expectation — if it materially exceeds it, pause and review.
- Data freshness (both Kraken price and Coin Metrics on-chain).

## 6. Configuration

- Strategy parameters are **frozen** in `src/quantbot/live/config.py:StrategyConfig`
  — changing them means trading a different, unvalidated strategy.
- Operational knobs (pair, mode, safety caps) are in `ExecutionConfig`.
- For BTC/GBP instead of BTC/USD, set `--pair XXBTZGBP`.
