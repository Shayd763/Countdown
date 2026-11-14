# Gatehouse

Gatehouse finds BRRR deals across England, residential and commercial, and shows you the ones that get
through all four financial gates. (The project folder is still called `kent-brrr-scout`.)

Every morning, a browser on your own laptop reads the listings added in the last day on Rightmove, Zoopla
and OnTheMarket, county by county (England's 47). It works out each one's postcode district, values it
against HM Land Registry sold prices and local rents, and checks it against your gates under the finance
plan you choose. You then filter by region, postcode area, postcode, property type and category on your
phone or laptop, and a morning alert tells you what passed.

**Scope.** England only for now. Wales has its own purchase tax instead of stamp duty (Land Transaction
Tax) and Scotland has another (LBTT plus the Additional Dwelling Supplement), so the gates would be wrong
there until those are modelled. The free Land Registry data doesn't cover Scotland either.

**Volume.** The England-wide run is about 500 to 800 page loads, roughly an hour, which is why the daily
run defaults to 05:30. That is far more than browsing Kent alone, so a portal is more likely to restrict
your connection for a while. It stops for the day at any block or captcha instead of trying to get around
it. You can switch portals or counties off in Settings → Coverage, or set the search back to your focus
postcodes only.

---

## Set up on your laptop (10 minutes)

1. **Get the files.** On GitHub, open the repo on the branch `claude/admiring-faraday-qfihxv` and click
   **Code → Download ZIP**. Unzip it and move the `kent-brrr-scout` folder into your **home folder**:
   - Mac: `/Users/<you>/kent-brrr-scout`
   - Windows: `C:\Users\<you>\kent-brrr-scout`

   On a Mac, don't put it in Documents, Desktop or Downloads. macOS stops scheduled jobs from reading
   those folders.
2. **Install Python 3.12 or newer** from <https://www.python.org/downloads/> if you don't already have it.
   On Windows, tick "Add python.exe to PATH". Having Google Chrome installed is recommended. If it isn't,
   the app downloads its own browser.
3. **Start the app.**
   - Mac: double-click **`Start Gatehouse.command`**. The first time, macOS may say it's from an
     unidentified developer. Right-click the file, choose **Open**, then **Open** again.
   - Windows: double-click **`Start Gatehouse (Windows).bat`**.
   - If double-clicking doesn't work, open Terminal and run `cd ~/kent-brrr-scout && bash start.sh`.

   The first start takes 2–5 minutes while it installs. After that it opens your browser at
   <http://127.0.0.1:8000> within a few seconds. Keep the black window open while you use the app.
4. **In the app:**
   1. Click **Load sample data** to explore. You get 17 invented listings and nothing is sent anywhere.
   2. Go to **Settings → API keys & alerts**. All keys are optional:
      - Telegram bot token and chat id, for alerts
      - Claude key, for better refurb-scope reading
      - PropertyData key, for paid comps
   3. In **Settings → Daily run**, pick a time and click **Install / update daily run**.
   4. Go to **Runs → Check setup**. This confirms the browser, each portal, your keys, Land Registry and
      the schedule.
   5. Click **Scan now**, pick one or two postcodes, and watch the first live run.

The daily run doesn't need the app window open. It runs by itself at the time you set:
- **Mac:** if the laptop is asleep at that time, it runs as soon as it wakes. If the laptop was switched
  off, that day's run is skipped.
- **Windows:** if the laptop was off at that time, it catches up the next time you log in.
- Open the app at any time to see results. They're saved in `data/brrr.db`.

---

## Phone app (iPhone & Android)

The phone app is installed from your browser. It doesn't come from the App Store (that would need a
$99/year Apple developer account). Once installed it opens full-screen from its own home-screen icon and
sends notifications like any other app.

It has four screens:
- **Today:** this morning's deals that pass all four gates, sorted by return. Each card shows cash to
  buy, refinance amount, money left in and monthly cashflow. Below them is a list of "negotiable" deals
  that would pass at a lower offer, with the offer price.
- **Deals:** search and filter every deal by postcode, category, sort order, the most cash you can put
  in, and minimum ROCE.
- **Deal page:**
  - The full numbers: cost to purchase, refinance and cash back, and monthly cashflow once the deal is
    complete.
  - An offer slider that re-runs the four gates as you drag it.
  - The four gates, refurb notes and risks.
  - Your pipeline status and notes, and links to share, open the listing or see it on a map.
- **Alerts & me:**
  - Notifications on or off, and whether to get the digest every morning or only when a deal passes.
  - The scan time.
  - Your own filters (postcodes, categories, maximum cash in, minimum ROCE, new listings only).
  - Your profile and password.

### Setup (once, about 10 minutes)

1. Start the app on your laptop as above. The first screen asks you to **create your profile** (name,
   email, password). That becomes the owner account.
2. Install **Tailscale** (free) on the laptop and on your phone, and sign in to both with the same
   account. Tailscale creates a private, encrypted link between your own devices. Nothing is put on the
   public internet.
3. On the laptop, go to **Settings → Phone app** and click **Turn on the secure phone address**. If
   Tailscale asks you to enable HTTPS certificates, click its link, approve, then click the button
   again. A QR code appears.
4. Scan the QR code with your phone's camera.
   - **iPhone:** open it in **Safari**, sign in, then tap **Share → Add to Home Screen**.
   - **Android:** open it in **Chrome**, sign in, then choose **Install app**.
5. Open the app from your home screen, go to **Alerts & me**, and switch on **Notifications on this
   phone**. Tap **Send me today's digest now** to check that notifications arrive.

Each morning the laptop scans, then pushes the digest to your phone. The digest is personalised to each
person's own filters. Notifications reach you anywhere, even when you're out of the house. Tapping one
opens Today, which shows that morning's results even if the laptop has gone back to sleep. Browsing
further and the offer slider need the laptop to be awake and online.

To add another person (for example a partner), use **Settings → People** on the laptop. They sign in
on their own phone with their own profile, filters and notifications.

---

## Where the data comes from (England-wide)

| What | Source | How often |
|---|---|---|
| New listings | Rightmove, Zoopla, OnTheMarket county searches, residential and commercial, listed in the last day | daily |
| Postcode district | the listing's address, else its map position via postcodes.io (free) | per listing |
| Sold prices | HM Land Registry Price Paid Data, kept on your laptop (about 300 MB; first download takes a few minutes) | monthly update |
| Rents and asking prices | one page of rentals and one of homes for sale per district, only where a listing could pass at Land Registry prices | cached 30 and 14 days |
| Commercial value | rent per sq ft by region (Settings) ÷ valuation yield: a rough first screen | — |
| Paid alternative | a PropertyData key replaces the local comps with PropertyData's | — |

Filters on both apps:
- **Region:** the nine English regions.
- **Postcode area:** for example M, LS, ME, built from the deals you have.
- **Postcode starts with:** any mix of areas, districts or sectors, such as `M, LS6, ME4 6`. An area like
  M matches M1 to M99 but not ME.
- **Property type** and **category** (residential, commercial, mixed-use).

Your morning alert uses the same choices, set in Alerts on the phone. Settings → Coverage on the laptop
chooses the counties, residential and commercial, how recent, the commercial price ceiling, and shows
the Land Registry data status. Region-wide rule changes go in `region_overrides` in the config: London,
for example, rarely reaches a 6.8% yield.

---

## Financing plans

Every deal can be costed under different ways of funding it. Pick one at the top of Today or
Deals (phone), or with **Financing** on the laptop's Deals screen. Every number, the four gates and
your morning alert follow that choice. Each deal's page also has **Ways to fund it**, which shows
every plan side by side: cash to buy, money left in, monthly cashflow, return on cash, and pass or fail.

| Plan | How you buy | Long-term loan | Owner |
|---|---|---|---|
| Bridge, then Ltd buy-to-let (default) | bridging loan | Ltd buy-to-let on the new value | Ltd company |
| Cash, then Ltd buy-to-let | your cash | Ltd buy-to-let | Ltd company |
| Ltd buy-to-let from day one | buy-to-let mortgage | remortgage on the new value | Ltd company |
| Bridge, then buy-to-let in your name | bridging loan | personal buy-to-let | you |
| Buy-to-let in your name from day one | buy-to-let mortgage | remortgage | you |
| Bridge, then commercial mortgage (Ltd) | bridging loan | commercial, repayment | Ltd company |
| Residential mortgage, live in then let | residential mortgage | residential remortgage, plus consent-to-let loading | you |
| All cash, no mortgage | your cash | none | Ltd company |

How each plan changes the maths:
- **Tax.** Ltd plans pay corporation tax on profit after interest. Plans in your own name pay income
  tax at your rate, and the mortgage interest only earns a 20% credit (Section 24).
- **Repayment loans.** On a repayment mortgage, the capital you repay counts against cashflow, but it
  isn't tax-deductible.
- **Living in it first.** The residential plan has no stamp duty surcharge, because it assumes the
  property is your main home when you buy.

**Rates and costs** are set per product in **Settings → Finance products** (laptop) or
**Alerts → Rates and costs** (phone; owner only). The products are Ltd buy-to-let, buy-to-let in your
name, commercial, residential and bridging. Each field shows a suggested value, researched in November
2026 with the sources listed. **Use suggested** fills those values in, and **Save** applies them.
Until you change them, the defaults are your original spec (5.25% buy-to-let, 0.85% a month bridging).

---

## What the daily run does

```
browser (on your laptop)
  → for each postcode and each portal: newest for-sale listings, up to 3 pages
      stops early when it reaches listings it saw yesterday
      rentals refreshed weekly, to build local rent comps
  → cheap pre-screen: excluded types, short leases, keywords
  → comps: PropertyData if you have a key, otherwise
           portal asking prices + portal rents + HM Land Registry sold prices
  → quick best-case gate check → only possible deals get their listing page opened
  → Claude reads the description and photos → refurb tier and red flags (optional)
  → the four gates (plain Python) → saved → Telegram alert for new passes
```

**The four gates**, which are editable in Settings:

1. **All-in cap.** Price + refurb + SDLT (inc. 5% surcharge) + fixed costs must be ≤ 75% of the
   conservative ARV.
2. **Gross yield.** Annual rent ÷ ARV must be at least 6.8%.
3. **SPV cashflow.** Cashflow must be positive after 75% LTV interest at 5.25%, 22% operating costs and
   19% corporation tax.
4. **Capital recycled.** Net refinance proceeds must be at least 55% of the total cash deployed.

### In the app

- **Deals**
  - Filter by postcode, category (residential, commercial, mixed-use), property type, pipeline status,
    "new since last run", ROCE, yield and price.
  - Click any deal for the full cost breakdown, a **what-if calculator** (try your offer price, refurb
    level, rent or ARV) and the max offer that still clears the 75% cap.
  - Mark deals as shortlisted / viewing / offer / rejected, and add notes.
  - **Export CSV**.
- **Runs**
  - Live progress, and how each portal did on its last run (ok, empty or blocked).
  - Run history, how much local comps data has been collected, the log, and **Check setup**.
- **Settings**
  - Daily schedule, portals on/off, "show the browser window", API keys, and every underwriting number.
  - **Re-score saved deals** applies changed numbers to deals you already have.

---

## Things to know

- **Rightmove, Zoopla and OnTheMarket don't allow automated collection in their terms.** This tool keeps
  to roughly what one person browsing would do: one pass a day, newest listings only, pauses of several
  seconds between pages. It never tries to get around a block or captcha. If a site serves one, it stops
  using that site for the run and the Runs tab tells you. The risk of a site restricting your connection
  for a while is yours to judge. Each portal can be switched off in Settings.
- **Portals change their page layout.** Extraction tries three methods (embedded data, structured data,
  then the visible listing cards), so small changes usually don't break it. If a portal shows **empty**
  on the Runs tab, a copy of the page has been saved in `data/debug/`. Send me that file and I can update
  the parser.
- **Comps without PropertyData start thin.** On the first runs some listings are skipped as "waiting for
  comps" until enough asking prices and rents have been seen for that type and size. This usually sorts
  itself out within a few daily runs. A PropertyData key avoids the wait.
- This is a screening tool, not advice. Always get a RICS valuation, a survey and a solicitor's review of
  the legal pack.

## For developers

```bash
pip install -e ".[dev]"
pytest                                         # 97 tests incl. real-browser portal run, push encryption vs RFC 8291
python -m src.main --sample --dry-run          # sample data, prints deal memos
python -m src.main --sources portals --outcode ME4 --dry-run
python -m src.main --daily                     # what the schedule runs
python -m src.scheduler install --time 07:30   # status | remove
python -m src.doctor                           # setup checks
python -m src.web --open
```

Layout:
- `src/underwriting.py`: the gates
- `src/portals/`: the browser collector
- `src/local_comps.py`: free comps
- `src/claude_analyst.py`: refurb classification
- `src/scheduler.py`: launchd, Task Scheduler and cron
- `src/web.py` + `src/static/index.html`: the app

Config lives in `config/postcodes.yaml`. Changes made on the Settings page go to `config/user.yaml`, and
keys go to `.env`.
