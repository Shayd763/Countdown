"""A real browser session on the user's own machine (Playwright).

Deliberately ordinary: a persistent profile (so cookie-consent choices stick),
UK locale, one tab, slow random pauses between pages. It does not disguise
itself or solve challenges — if a site serves a block/captcha page, `fetch`
reports it and the collector stops using that site for the run.
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from .extract import detect_block

log = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "data"
PROFILE_DIR = DATA_DIR / "browser-profile"
DEBUG_DIR = DATA_DIR / "debug"

# JS run in the page: every link to a detail page + the text of its listing card.
_CARDS_JS = r"""
(pattern) => {
  const re = new RegExp(pattern);
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    const m = href && href.match(re);
    if (!m || seen.has(m[1])) continue;
    // climb to the largest ancestor that still only links to this one listing
    let node = a, best = a;
    for (let i = 0; i < 8 && node.parentElement; i++) {
      node = node.parentElement;
      const ids = new Set([...node.querySelectorAll('a[href]')]
        .map(x => (x.getAttribute('href') || '').match(re)).filter(Boolean).map(x => x[1]));
      if (ids.size > 1) break;
      best = node;
    }
    const text = best.innerText || '';
    if (!text.includes('£')) continue;
    seen.add(m[1]);
    out.push({ href, text: text.slice(0, 2000) });
  }
  return out;
}
"""

_CONSENT_BUTTONS = re.compile(r"^\s*(reject all|reject|necessary only|accept all|accept|allow all|"
                              r"i accept|agree)\s*$", re.I)


@dataclass
class PageResult:
    url: str
    status: int | None
    html: str = ""
    text: str = ""
    title: str = ""
    cards: list[dict[str, Any]] = field(default_factory=list)
    blocked: str | None = None
    error: str | None = None


class PortalBrowser:
    def __init__(self, cfg: dict[str, Any]):
        pc = cfg["portals"]
        self.headless = bool(pc.get("headless", True))
        self.channel = pc.get("browser_channel") or None
        lo, hi = pc.get("page_delay_seconds", [4, 9])
        self.delay = (float(lo), float(hi))
        self._pw = None
        self._ctx = None
        self.page = None
        self._last_fetch = 0.0
        self._consented: set[str] = set()

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> PortalBrowser:
        from playwright.sync_api import sync_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        opts: dict[str, Any] = dict(
            user_data_dir=str(PROFILE_DIR), headless=self.headless, locale="en-GB",
            timezone_id="Europe/London", viewport={"width": 1366, "height": 900},
        )
        exe = os.environ.get("BRRR_CHROMIUM_PATH")
        attempts = ([{"executable_path": exe}] if exe else []) + (
            [{"channel": self.channel}] if self.channel else []) + [{}]
        last_exc: Exception | None = None
        for extra in attempts:
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(**opts, **extra)
                break
            except Exception as exc:  # noqa: BLE001 — try the next browser option
                last_exc = exc
        if self._ctx is None:
            self._pw.stop()
            raise RuntimeError(
                "Could not start a browser. Install Google Chrome, or run "
                "`python -m playwright install chromium`.") from last_exc
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.page.set_default_timeout(45_000)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    # -- fetching ---------------------------------------------------------

    def _pause(self) -> None:
        wait = random.uniform(*self.delay) - (time.monotonic() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)

    def _maybe_consent(self, host: str) -> None:
        if host in self._consented:
            return
        self._consented.add(host)
        try:
            for frame in [self.page.main_frame, *self.page.frames[1:4]]:
                for btn in frame.get_by_role("button").all()[:40]:
                    label = (btn.inner_text(timeout=500) or "").strip()
                    if _CONSENT_BUTTONS.match(label):
                        btn.click(timeout=2000)
                        self.page.wait_for_timeout(800)
                        return
        except Exception:  # noqa: BLE001 — consent banners are optional
            pass

    def fetch(self, url: str, *, detail_pattern: str | None = None, polite: bool = True,
              cards: bool = True) -> PageResult:
        if polite and self._last_fetch:
            self._pause()
        self._last_fetch = time.monotonic()
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded")
            try:
                self.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001 — busy pages never go fully idle
                pass
            host = re.sub(r"^https?://([^/]+).*", r"\1", url)
            self._maybe_consent(host)
            title = self.page.title()
            text = self.page.inner_text("body") if self.page.locator("body").count() else ""
            res = PageResult(url=url, status=resp.status if resp else None, html=self.page.content(),
                             text=text, title=title)
            res.blocked = detect_block(res.status, title, text)
            if cards and detail_pattern and not res.blocked:
                # gently scroll so lazy-loaded cards render
                for _ in range(4):
                    self.page.mouse.wheel(0, 2200)
                    self.page.wait_for_timeout(random.randint(350, 800))
                res.cards = self.page.evaluate(_CARDS_JS, detail_pattern)
                res.html = self.page.content()
            return res
        except Exception as exc:  # noqa: BLE001
            return PageResult(url=url, status=None, error=f"{type(exc).__name__}: {exc}")

    def fetch_json(self, url: str, *, polite: bool = True) -> Any:
        """GET a JSON API through the browser profile (same cookies), asking for JSON explicitly.
        Some endpoints (e.g. Rightmove typeahead) answer a normal page load with XML."""
        if polite and self._last_fetch:
            self._pause()
        self._last_fetch = time.monotonic()
        try:
            resp = self._ctx.request.get(url, headers={"Accept": "application/json"})
            return resp.json() if resp.ok else None
        except Exception:  # noqa: BLE001
            return None

    def dump_debug(self, label: str, res: PageResult) -> str:
        """Save HTML + screenshot so a broken parser can be fixed later."""
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stem = DEBUG_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{label}"
        Path(f"{stem}.html").write_text(res.html or res.text or "", encoding="utf-8")
        try:
            self.page.screenshot(path=f"{stem}.png", full_page=False)
        except Exception:  # noqa: BLE001
            pass
        return str(stem)
