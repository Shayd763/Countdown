# Kent BRRR Scout

This app runs on your own laptop. Every morning it reads the newest Rightmove, Zoopla and OnTheMarket
listings for Medway (`ME1 ME2 ME4 ME5 ME7 ME8`) and Thanet (`CT8 CT9 CT10 CT11 CT12`). It checks each one
against your four BRRR gates and shows the results in a web app on your laptop. When a deal passes, it can
also send you a Telegram message.

The portal pages are read in an ordinary browser on your home internet connection. These sites block
cloud servers, but this is just the same pages you would look at yourself, read once a day at a slow,
human pace.

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
   - Mac: double-click **`Start Kent BRRR Scout.command`**. The first time, macOS may say it's from an
     unidentified developer. Right-click the file, choose **Open**, then **Open** again.
   - Windows: double-click **`Start Kent BRRR Scout (Windows).bat`**.
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
pytest                                         # 62 tests incl. a real-browser run against mock portals
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
