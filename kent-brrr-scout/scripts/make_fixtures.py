"""Regenerate tests/fixtures/*.json — SYNTHETIC sample payloads shaped like PropertyData
responses, for offline runs and tests. Numbers are plausible for Medway/Thanet but invented."""

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)
rng = random.Random(42)

# outcode: (terraced 3-bed sold median, £/sqft, rent by beds {1,2,3,4})
MARKET = {
    "ME1": (265_000, 300, {1: 925, 2: 1250, 3: 1525, 4: 1800}),
    "ME2": (285_000, 310, {1: 950, 2: 1275, 3: 1550, 4: 1850}),
    "ME4": (255_000, 290, {1: 950, 2: 1300, 3: 1600, 4: 1850}),
    "ME5": (295_000, 305, {1: 925, 2: 1275, 3: 1550, 4: 1850}),
    "ME7": (250_000, 285, {1: 925, 2: 1275, 3: 1550, 4: 1800}),
    "ME8": (300_000, 315, {1: 950, 2: 1300, 3: 1575, 4: 1900}),
    "CT8": (275_000, 320, {1: 850, 2: 1150, 3: 1400, 4: 1700}),
    "CT9": (255_000, 330, {1: 900, 2: 1250, 3: 1550, 4: 1800}),
    "CT10": (300_000, 340, {1: 900, 2: 1225, 3: 1500, 4: 1800}),
    "CT11": (285_000, 335, {1: 900, 2: 1300, 3: 1550, 4: 1800}),
    "CT12": (260_000, 300, {1: 850, 2: 1150, 3: 1400, 4: 1700}),
}
TYPE_FACTOR = {"flat": 0.55, "terraced_house": 1.0, "semi-detached_house": 1.18,
               "detached_house": 1.5}
BED_FACTOR = {1: 0.7, 2: 0.86, 3: 1.0, 4: 1.2}

sold_prices, ppsf, rents, prices = {}, {}, {}, {}
for oc, (median3, psf, rent) in MARKET.items():
    raw = []
    for ptype, tf in TYPE_FACTOR.items():
        for beds, bf in BED_FACTOR.items():
            if ptype == "flat" and beds > 3:
                continue
            for _ in range(5):
                raw.append({"type": ptype, "bedrooms": beds, "tenure": "freehold",
                            "price": round(median3 * tf * bf * rng.uniform(0.9, 1.1), -3),
                            "date": f"2026-{rng.randint(1, 10):02d}-15"})
    sold_prices[oc] = {"status": "success", "data": {"points_analysed": len(raw), "raw_data": raw}}
    ppsf[oc] = {"status": "success", "data": {"average": psf, "70pc_range": [psf * 0.85, psf * 1.15]}}
    rents[oc] = {"by_bedrooms": {
        str(b): {"status": "success", "data": {"long_let": {
            "units": "gbp_per_month", "average": r, "70pc_range": [r * 0.9, r * 1.1]}}}
        for b, r in rent.items()} | {"default": {"status": "success", "data": {"long_let": {
            "units": "gbp_per_month", "average": rent[2], "70pc_range": [rent[2] * .9, rent[2] * 1.1]}}}}}
    prices[oc] = {"by_type": {
        t: {"by_bedrooms": {
            str(b): {"status": "success", "data": {"average": round(median3 * tf * BED_FACTOR[b] * 1.04, -3)}}
            for b in BED_FACTOR} | {"default": {"status": "success", "data": {"average": median3 * tf}}}}
        for t, tf in TYPE_FACTOR.items()} | {"default": {"by_bedrooms": {
            "default": {"status": "success", "data": {"average": median3}}}}}}

for name, data in [("sold_prices", sold_prices), ("sold_prices_per_sqf", ppsf), ("rents", rents),
                   ("prices", prices)]:
    data["default"] = data["ME4"]
    (OUT / f"{name}.json").write_text(json.dumps({"by_outcode": data}, indent=1))

(OUT / "floor_areas.json").write_text(json.dumps({"status": "success", "data": {"known_floor_areas": []}}))
(OUT / "energy_efficiency.json").write_text(json.dumps({"status": "success", "data": {"energy_efficiency": []}}))

L = []
def add(id_, lst, pc, addr, ptype, beds, price, sqf, desc, tenure="", lease=None, dom=30):
    L.append({"id": id_, "_list": lst, "url": f"https://example.com/listing/{id_}",
              "postcode": pc, "address": addr, "type": ptype, "bedrooms": beds,
              "price": price, "sqf": sqf, "days_on_market": dom, "tenure": tenure,
              "lease_years_remaining": lease, "description": desc, "images": []})

add("1001", "repossessed-properties", "ME4 5QA", "12 Example Street, Chatham", "terraced_house", 3, 112_000, 920,
    "Repossessed three bedroom Victorian terrace in need of modernisation throughout. Dated kitchen and bathroom. Freehold. Cash buyers preferred.", "Freehold")
add("1002", "unmodernised-properties", "ME7 2AB", "48 Sample Road, Gillingham", "terraced_house", 2, 92_000, 720,
    "Two bedroom terrace requiring complete renovation, full rewire needed, no central heating. Probate sale. Freehold.", "Freehold", dom=90)
add("1003", "reduced-properties", "ME1 1XY", "7 Placeholder Close, Rochester", "semi-detached_house", 3, 225_000, 1010,
    "Well presented semi-detached house, recently refitted kitchen. Freehold.", "Freehold")
add("1004", "auction-properties", "CT9 1AA", "Flat 3, 20 Demo Terrace, Margate", "flat", 1, 42_000, 470,
    "One bedroom flat requiring updating. Leasehold with 120 years remaining. Auction lot — see special conditions.", "Leasehold", 120)
add("1005", "auction-properties", "CT9 2BB", "Flat 1, 4 Mock Parade, Margate", "flat", 2, 70_000, 640,
    "Two bedroom flat, leasehold 68 years remaining, needs modernisation.", "Leasehold", 68)
add("1006", "reduced-properties", "CT11 9ZZ", "Flat 12, Sample Court, Ramsgate", "flat", 1, 55_000, 450,
    "Retirement apartment for the over 60s. Leasehold 99 years remaining.", "Leasehold", 99)
add("1007", "cash-buyers-only", "CT12 6AA", "3 Fictional Way, Minster", "terraced_house", 3, 128_000, 900,
    "Shared ownership 50% share of a three bedroom house.", "Leasehold", 120)
add("1008", "unmodernised-properties", "CT10 3CD", "21 Example Avenue, Broadstairs", "terraced_house", 3, 150_000, 950,
    "Three bedroom terrace in need of modernisation, new kitchen and bathroom required. Freehold.", "Freehold")
add("1009", "repossessed-properties", "ME8 0AA", "9 Test Lane, Rainham", "detached_house", 4, 260_000, 1500,
    "Detached four bedroom house, requires updating.", "Freehold")
add("1010", "cash-buyers-only", "CT8 8EF", "15 Sample Gardens, Westgate-on-Sea", "terraced_house", 2, 105_000, 700,
    "Two bedroom cottage suffering from subsidence, underpinning required. Cash buyers only. Freehold.", "Freehold")
add("1011", "unmodernised-properties", "ME2 4GH", "33 Mock Street, Strood", "terraced_house", 3, 145_000, 900,
    "Three bed terrace, dated throughout, needs modernisation. Freehold.", "Freehold")
add("1012", "auction-properties", "ME5 9JK", "2 Example Drive, Walderslade", "semi-detached_house", 3, 160_000, 980,
    "Semi detached house requiring full refurbishment. Freehold. Vacant possession.", "Freehold")
add("1013", "reduced-properties", "ME4 4LM", "Ground floor shop, 88 Demo High Street, Chatham", "commercial", 0, 95_000, 1100,
    "Lock-up retail unit on the high street, vacant, needs refit. Freehold.", "Freehold")
add("1014", "auction-properties", "CT9 1NP", "61 Placeholder Road, Margate", "mixed-use", 2, 165_000, 1600,
    "Mixed use: ground floor shop with two bedroom flat above, requires modernisation. Freehold.", "Freehold")
add("1015", "unmodernised-properties", "CT11 7QR", "5 Test Terrace, Ramsgate", "terraced_house", 2, 99_000, 680,
    "Two bedroom terrace requiring updating, tenanted. Freehold.", "Freehold")
add("1016", "repossessed-properties", "ME7 5ST", "Flat 8, Mock House, Gillingham", "flat", 2, 46_000, 620,
    "Repossessed two bedroom flat needing new kitchen and bathroom. Leasehold, 115 years remaining.", "Leasehold", 115)
add("1017", "reduced-properties", "CT12 5UV", "Plot 4, Sample Park, Minster", "terraced_house", 2, 45_000, 500,
    "Park home on residential site. Over 55s only.", "")

(OUT / "sourced_properties.json").write_text(json.dumps({"status": "success", "properties": L}, indent=1))

rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>Sample auction feed</title>
<item><title>Lot 14: 27 Example Road, Chatham ME4 6AB</title><link>https://example.com/lot/14</link>
<description>Guide price £95,000+ Two bed terraced house in need of complete renovation. Freehold.</description></item>
<item><title>Lot 15: Land at Somewhere, Maidstone ME15 1AA</title><link>https://example.com/lot/15</link>
<description>Guide price £50,000 Parcel of land.</description></item>
</channel></rss>"""
(OUT / "auction_feed.xml").write_text(rss)
print(f"wrote {len(L)} listings")
