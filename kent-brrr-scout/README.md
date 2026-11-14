# Kent BRRR Scout

Finds distressed residential (and optionally commercial) listings in Medway (`ME1 ME2 ME4 ME5 ME7 ME8`)
and Thanet (`CT8 CT9 CT10 CT11 CT12`). It checks each one against four fixed financial gates, which are
plain Python and never handed to the LLM. Deals that pass go into SQLite and out as a Telegram or webhook
alert. A local web UI lets you browse and filter deals by postcode.

```
ingest (PropertyData sourced lists + auction RSS)
  → eligibility pre-screen (keywords, type, lease)    ← no API spend on excluded stock
  → enrich (sold comps, £/sqft, rents, floor area; Land Registry + EPC fallbacks)
  → best-case gate check with light refurb            ← skips the Claude call on hopeless deals
  → Claude: refurb tier + red flags + tenure clues (structured output)
  → underwrite (SDLT, bridging, refi, ICR, cashflow, ROCE)  — pure Python
  → SQLite (dedup, price-drop history, audit log) → alert
```

## Quick start

```bash
cd kent-brrr-scout
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # add your keys

python -m src.main --offline --dry-run        # synthetic sample data, no network, prints memos
python -m src.web                             # UI at http://127.0.0.1:8000
pytest                                        # 47 tests, incl. exact SDLT/bridging/ICR/refi maths
```

Live run:

```bash
python -m src.main                         # all outcodes, sends alerts
python -m src.main --outcode ME4 --outcode CT9 --min-roce 10 --dry-run
python -m src.main --no-claude             # keyword heuristic instead of Claude
```

### Scheduling

Cron example: weekdays at 07:40 and 17:40.

```cron
40 7,17 * * 1-5  cd /path/to/kent-brrr-scout && .venv/bin/python -m src.main >> data/cron.log 2>&1
```

Alerts are sent once per deal. A deal is re-sent only if its price falls after the first alert.

## The four gates (`src/underwriting.py`)

| Gate | Rule | Config key |
|---|---|---|
| 1. All-in cap | `asking + refurb + SDLT (inc. 5% surcharge) + fixed costs ≤ 75% × conservative ARV` | `all_in_cap_pct_of_arv` |
| 2. Gross yield | `annual rent / conservative ARV ≥ 6.8%` | `min_gross_yield` |
| 3. SPV cashflow | `rent − 22% opex − (75% LTV × 5.25%) − 19% CT > £0` | `min_net_monthly_cashflow` |
| 4. Capital recycled | `(refi loan − refi costs) / total cash deployed ≥ 55%` | `min_capital_recycled_pct` |

Definitions:
- **Total cash deployed** = gate-1 all-in + bridging costs + refinance costs.
- **Money left in** = total deployed − refi loan.
- **ROCE** = net annual cashflow / money left in. It shows as **∞** when all the money comes back out.
- **Max offer** is the highest asking price that still clears gate 1, rounded down to the nearest £500.
- **ICR** (125% at a 5.5% stress rate) is reported for every deal. It limits the loan only if you set
  `icr_caps_loan: true`.
- **SDLT** uses the bands in force from 1 April 2025 plus the 5% additional-dwelling surcharge (none below
  £40k). Commercial and mixed-use stock uses non-residential rates.

**Conservative ARV** is the median of the available estimates, minus a haircut (5% by default, 7% for
CT9). The estimates are: sold-comps median, sold £/sqft × floor area, and asking average − 5%.
**Rent** is the PropertyData long-let average minus 5%. Commercial stock is valued from config: rent per
sq ft divided by the valuation yield.

Every threshold and cost is in `config/postcodes.yaml`, which can be overridden per outcode.

## Claude's role (`src/claude_analyst.py`)

Claude (`claude-opus-5-5`, structured output via `client.beta.messages.parse`) reads the description,
photos and floorplans. It returns a `RefurbAssessment`:
- refurb tier (`light`, `medium`, `heavy` or `structural`)
- confidence and key works
- red flags (knotweed, short lease, sitting tenant, auction special conditions, …)
- tenure and lease length, if the listing states them
- whether the property is an excluded type

Python turns the tier into a cost (£/sqft × area, with a minimum per tier, plus 10% contingency).
Server-side refusal fallback (`fallbacks: "default"`) is switched on. If Claude is unavailable or
declines, the keyword heuristic produces the same schema.

## Web UI

The UI has:
- Postcode chips grouped Medway / Thanet, plus one summary card per outcode (passing count, average
  yield, best ROCE).
- Filters: category (residential / commercial / mixed-use), property type, passes-all-gates,
  include-ineligible, minimum ROCE, minimum yield and maximum price.
- Sorting by ROCE, cashflow, yield, discount to ARV, price drop or cheapest.
- A detail drawer for each deal: full cost stack, each gate with its figures, refurb scope, red flags,
  comps and price history.
- **Run scan**, which runs the pipeline for the selected outcodes. Scans from the UI never send alerts.
- **Sample data**, which uses the bundled fixtures.

API: `GET /api/deals`, `GET /api/deals/{id}`, `GET /api/summary`, `GET /api/meta`, `POST /api/scan`,
`GET /api/audit`.

## Check before going live

- **PropertyData list names.** The `sourced_lists` slugs in the config (`repossessed-properties`,
  `unmodernised-properties`, …) need checking against your plan's `/sourced-properties` docs. The
  response parsing accepts several field-name variants, but was written against the documented response
  shape, not a live response.
- **RSS feeds.** `https://www.cliveemson.co.uk/feed/` is a placeholder. Swap in the auction house's
  actual RSS/Atom URL. Any feed works if its items include a postcode and a £ price.
- **EPC API.** It uses Open Data Communities Basic auth (`EPC_API_EMAIL` + `EPC_API_TOKEN`). If you are
  on the newer gov.uk EPC service, update `epc_base_url`.
- **Fixtures.** `tests/fixtures/*.json` are synthetic: invented addresses with plausible Kent numbers,
  for offline runs and tests. Regenerate them with `python scripts/make_fixtures.py`.
- The defaults (refurb £/sqft, bridging terms, refi fees, 19% CT) are assumptions. Tune them to your
  broker's and contractors' real numbers.

This is a screening tool, not advice. Always get a RICS valuation, a survey and a solicitor's review of
the legal pack before buying.
