"""Deal memo formatting + delivery via Telegram Bot API or a generic JSON webhook."""

from __future__ import annotations

import logging
import os
import re

import httpx

from .models import Deal

log = logging.getLogger(__name__)

_MD2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def md2(text: object) -> str:
    """Escape text for Telegram MarkdownV2."""
    return _MD2_SPECIAL.sub(r"\\\1", str(text))


def _gbp(x: float) -> str:
    return f"£{x:,.0f}"


def format_memo(deal: Deal, *, telegram: bool = False) -> str:
    """Markdown deal memo. With telegram=True, values are MarkdownV2-escaped."""
    lst, r, rf, c = deal.listing, deal.result, deal.refurb, deal.comps
    e = md2 if telegram else str
    b = (lambda s: f"*{s}*") if telegram else (lambda s: f"**{s}**")
    tick = {True: "✅", False: "❌"}
    lines = [
        b(e(f"BRRR deal — {lst.outcode} {lst.property_type}"
            f"{f' {lst.bedrooms}-bed' if lst.bedrooms else ''}")),
        e(lst.address or lst.postcode),
    ]
    if lst.url:
        lines.append(f"[{e('View listing')}]({lst.url})" if telegram else f"[View listing]({lst.url})")
    if deal.price_drop_pct:
        lines.append(e(f"🔻 Price reduced {deal.price_drop_pct:.1f}% since first seen"))
    lines += [
        "",
        e(f"Asking {_gbp(r.asking_price)} · ARV {_gbp(r.conservative_arv)} · "
          f"Max offer {_gbp(r.max_offer_price)}"),
        e(f"Refurb: {r.refurb_tier} ({rf.confidence} conf.) {_gbp(r.refurb_cost)}"),
        e(f"SDLT {_gbp(r.sdlt)} · Bridging {_gbp(r.bridging_costs)} · Refi costs {_gbp(r.refi_costs)}"),
        e(f"Total deployed {_gbp(r.total_cash_deployed)} · Refi loan {_gbp(r.refi_loan)} · "
          f"Left in {_gbp(r.money_left_in)}"),
        e(f"Rent {_gbp(r.annual_rent / 12)}/mo · Gross yield {r.gross_yield:.2%} · ICR {r.icr:.2f}x"),
        b(e(f"Net cashflow {_gbp(r.net_monthly_cashflow)}/mo · ROCE {r.roce_display} · "
            f"Recycled {r.capital_recycled_pct:.0%}")),
        "",
        b(e("Gates")),
        *[e(f"{tick[g.passed]} {g.name}: {g.detail}") for g in r.gates],
    ]
    if rf.key_works:
        lines += ["", b(e("Works")), *[e(f"• {w}") for w in rf.key_works[:6]]]
    if rf.red_flags:
        lines += ["", b(e("Red flags")), *[e(f"⚠️ {f}") for f in rf.red_flags[:6]]]
    lines += ["", e(f"Comps: {', '.join(c.sources)}")]
    return "\n".join(lines)


class Notifier:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
        self.webhook = os.environ.get("ALERT_WEBHOOK_URL")

    @property
    def configured(self) -> bool:
        return bool((self.tg_token and self.tg_chat) or self.webhook)

    def send(self, deal: Deal) -> bool:
        if self.dry_run or not self.configured:
            print("\n" + "─" * 72 + "\n" + format_memo(deal) + "\n")
            return False
        ok = False
        with httpx.Client(timeout=20) as http:
            if self.tg_token and self.tg_chat:
                try:
                    resp = http.post(
                        f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                        json={"chat_id": self.tg_chat, "text": format_memo(deal, telegram=True),
                              "parse_mode": "MarkdownV2", "disable_web_page_preview": False},
                    )
                    resp.raise_for_status()
                    ok = True
                except httpx.HTTPError as exc:
                    log.error("Telegram send failed: %s", exc)
            if self.webhook:
                try:
                    resp = http.post(self.webhook, json={
                        "text": format_memo(deal), "deal": deal.model_dump(mode="json")})
                    resp.raise_for_status()
                    ok = True
                except httpx.HTTPError as exc:
                    log.error("Webhook send failed: %s", exc)
        return ok
