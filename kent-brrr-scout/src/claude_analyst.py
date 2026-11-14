"""Claude refurb-scope classifier.

Claude reads the listing text plus photos/floorplans and returns a *structured*
`RefurbAssessment` (tier, works, red flags, tenure clues). It never sees or
decides on the financial gates — `underwriting.py` prices the tier and decides.

When no API key is available (or `--no-claude`), a keyword heuristic produces
the same schema so the pipeline still runs offline.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import anthropic

from .models import RawListing, RefurbAssessment

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a UK residential refurbishment surveyor assessing BRRR \
(buy, refurbish, refinance, rent) acquisitions in Medway and Thanet, Kent.

From the listing text and any photos or floorplans, classify the refurbishment scope \
needed to bring the property to a lettable, mortgageable standard (EPC C target, \
compliant electrics, working kitchen and bathroom):

- light: cosmetic only — decoration, flooring, minor fixes.
- medium: new kitchen and/or bathroom, partial rewire, new boiler, decoration.
- heavy: full strip-out — rewire, replumb, heating, windows, plastering, layout changes.
- structural: subsidence/underpinning, roof replacement, significant damp/rot, fire \
damage, extensions or conversion works.

Judge from evidence, not estate-agent language: "needs modernisation" alone usually means \
medium; "in need of complete renovation", "cash buyers only", or visible ceiling/roof \
damage points higher. When evidence is thin, say so with low confidence rather than guessing \
high or low.

Also report:
- red_flags: anything a lender, surveyor or solicitor would worry about (short lease, \
non-standard construction, Japanese knotweed, flying freehold, flood zone, sitting tenant, \
cladding, ground rent escalation, auction special conditions, mortgage-ineligible).
- detected_tenure and detected_lease_years if the text states them (null if not stated).
- is_excluded_type: true if this is retirement/age-restricted housing, shared ownership, \
a houseboat, a park/mobile home or a holiday lodge; give exclusion_reason.

You do not assess price, value, rent or whether it is a good deal — that is done elsewhere."""


def _build_content(listing: RawListing, max_images: int) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for url in [*listing.floorplan_urls, *listing.image_urls][:max_images]:
        if url.startswith("http"):
            content.append({"type": "image", "source": {"type": "url", "url": url}})
    facts = [
        f"Address: {listing.address or 'n/a'} ({listing.postcode})",
        f"Type: {listing.property_type}; category: {listing.category.value}",
        f"Bedrooms: {listing.bedrooms if listing.bedrooms is not None else 'n/a'}",
        f"Floor area: {f'{listing.floor_area_sqft:.0f} sq ft' if listing.floor_area_sqft else 'n/a'}",
        f"Tenure (from feed): {listing.tenure.value}",
        f"Source: {listing.source}{' (auction)' if listing.is_auction else ''}",
    ]
    content.append({
        "type": "text",
        "text": "Listing facts:\n" + "\n".join(facts)
        + f"\n\nListing description:\n<description>\n{listing.description or '(none)'}\n</description>"
        + ("\n\nThe images above are this listing's photos/floorplans." if len(content) else ""),
    })
    return content


class ClaudeAnalyst:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium", max_images: int = 6):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_images = max_images

    def _call(self, content: list[dict[str, Any]]) -> RefurbAssessment | None:
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # re-run on a fallback model if a safety classifier declines
            output_config={"effort": self.effort},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_format=RefurbAssessment,
        )
        if response.stop_reason == "refusal":
            log.warning("Claude declined listing (%s)", getattr(response.stop_details, "category", None))
            return None
        return response.parsed_output

    def assess(self, listing: RawListing) -> RefurbAssessment:
        content = _build_content(listing, self.max_images)
        try:
            result = self._call(content)
        except anthropic.BadRequestError as exc:
            # Most often an image URL the API couldn't fetch — retry text-only.
            log.info("retrying %s without images: %s", listing.listing_id, exc.message)
            try:
                result = self._call([c for c in content if c["type"] == "text"])
            except anthropic.APIError as exc2:
                log.warning("Claude failed for %s: %s", listing.listing_id, exc2)
                result = None
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            log.warning("Claude unavailable for %s: %s", listing.listing_id, exc)
            result = None
        return result or heuristic_assessment(listing)


# ---------------------------------------------------------------------------
# Offline heuristic (same output schema)
# ---------------------------------------------------------------------------

_TIER_SIGNALS: list[tuple[str, list[str]]] = [
    ("structural", ["subsidence", "underpin", "structural", "fire damage", "roof needs replac",
                    "collapsed", "dry rot", "uninhabitable", "derelict"]),
    ("heavy", ["complete renovation", "full renovation", "full refurbishment", "total refurb",
               "requires complete", "rewire", "shell", "gutted", "no central heating",
               "cash buyers only", "non-mortgageable", "unmortgageable"]),
    ("medium", ["modernisation", "modernization", "updating", "dated", "new kitchen",
                "new bathroom", "refurbishment", "improvement", "tlc", "unmodernised", "refit"]),
]

_RED_FLAG_SIGNALS = {
    "japanese knotweed": "Japanese knotweed mentioned",
    "flying freehold": "Flying freehold",
    "sitting tenant": "Sitting tenant",
    "tenanted": "Sold with tenant in situ",
    "non-standard": "Non-standard construction",
    "flood": "Possible flood risk",
    "cladding": "Cladding / EWS1 risk",
    "special conditions": "Auction special conditions — check legal pack",
    "cash buyers only": "Likely unmortgageable as-is (bridging required)",
}

_EXCLUDE = ["retirement", "over 55", "over 60", "shared ownership", "houseboat", "park home",
            "mobile home", "holiday lodge"]


def heuristic_assessment(listing: RawListing) -> RefurbAssessment:
    text = f"{listing.address} {listing.description}".lower()
    tier, hits = "light", []
    for name, words in _TIER_SIGNALS:
        found = [w for w in words if w in text]
        if found:
            tier, hits = name, found
            break
    red = [msg for kw, msg in _RED_FLAG_SIGNALS.items() if kw in text]
    excl = next((w for w in _EXCLUDE if w in text), None)
    lease = None
    m = re.search(r"(\d{2,3})\s*(?:years?|yrs?)\s*(?:remaining|left|unexpired)", text)
    if m:
        lease = int(m.group(1))
    if "share of freehold" in text:
        tenure = "share-of-freehold"
    elif "leasehold" in text or lease:
        tenure = "leasehold"
    elif "freehold" in text:
        tenure = "freehold"
    else:
        tenure = "unknown"
    return RefurbAssessment(
        tier=tier,  # type: ignore[arg-type]
        confidence="low" if not hits else "medium",
        key_works=[f"Signal: '{h}'" for h in hits] or ["No refurb signals — assumed cosmetic"],
        red_flags=red,
        detected_tenure=tenure,  # type: ignore[arg-type]
        detected_lease_years=lease,
        is_excluded_type=excl is not None,
        exclusion_reason=f"keyword '{excl}'" if excl else None,
        rationale="Offline keyword heuristic (Claude not used).",
    )


def make_analyst(cfg: dict[str, Any], use_claude: bool) -> ClaudeAnalyst | None:
    if not use_claude:
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        log.warning("No Anthropic credentials — using offline refurb heuristic")
        return None
    c = cfg.get("claude", {})
    return ClaudeAnalyst(c.get("model", "claude-opus-5-5"), c.get("effort", "medium"),
                         c.get("max_images", 6))
