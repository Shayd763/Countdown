"""Local stand-in for the three portals, for exercising the browser collector offline.

- "rightmove": data embedded as __NEXT_DATA__ JSON, plus detail pages with window.PAGE_MODEL
- "zoopla": no JSON at all — DOM listing cards only
- "onthemarket": serves a challenge page, to check block detection

Synthetic data only. Run standalone with `python -m tests.portal_mock` (port 8790).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RM_SALE = [
    {"id": 90001, "bedrooms": 3, "displayAddress": "14 Mock Road, Chatham ME4 6XX",
     "propertySubType": "Terraced", "summary": "Three bed terrace in need of modernisation. Freehold.",
     "price": {"amount": 112000, "frequency": "not specified",
               "displayPrices": [{"displayPrice": "£112,000"}]},
     "propertyUrl": "/properties/90001#/?channel=RES_BUY",
     "propertyImages": {"images": [{"srcUrl": "https://img.example.com/90001-1.jpg"}]},
     "location": {"latitude": 51.38, "longitude": 0.53}},
    {"id": 90002, "bedrooms": 2, "displayAddress": "3 Sample Street, Chatham", "propertySubType": "Flat",
     "summary": "Purpose built flat, retirement living for over 60s.",
     "price": {"amount": 85000}, "propertyUrl": "/properties/90002"},
    {"id": 90003, "bedrooms": 4, "displayAddress": "77 Example Hill, Chatham",
     "propertySubType": "Detached", "summary": "Detached family home.",
     "price": {"amount": 340000}, "propertyUrl": "/properties/90003"},
]
RM_RENT = [
    {"id": 91000 + i, "bedrooms": b, "displayAddress": f"{i} Rent Row, Chatham",
     "propertySubType": "Terraced", "price": {"amount": p, "frequency": "monthly"},
     "propertyUrl": f"/properties/{91000 + i}"}
    for i, (b, p) in enumerate([(3, 1550), (3, 1600), (3, 1650), (2, 1300), (2, 1275), (2, 1325)])
] + [{"id": 91100, "bedrooms": 3, "displayAddress": "9 Weekly Way, Chatham", "propertySubType": "Terraced",
      "price": {"amount": 375, "frequency": "weekly"}, "propertyUrl": "/properties/91100"}]

RM_DETAIL = {
    "90001": {"propertyData": {
        "text": {"description": "<p>A three bedroom Victorian terrace requiring full modernisation: "
                                "dated kitchen and bathroom, partial rewire recommended. Chain free. "
                                "Approximately 915 sq ft.</p>"},
        "keyFeatures": ["Three bedrooms", "Needs modernisation", "Freehold", "Garden"],
        "tenure": {"tenureType": "FREEHOLD"},
        "images": [{"url": "https://img.example.com/90001-1.jpg"},
                   {"url": "https://img.example.com/90001-2.jpg"}],
        "floorplans": [{"url": "https://img.example.com/90001-plan.png"}],
    }},
}

# County (REGION) search results: listings across England plus one over the Welsh border
RM_REGION_SALE = [
    {"id": 92001, "bedrooms": 3, "displayAddress": "22 Test Grove, Manchester M14 5AB",
     "propertySubType": "Terraced", "summary": "Three bed terrace needing full modernisation.",
     "price": {"amount": 98000}, "propertyUrl": "/properties/92001"},
    {"id": 92002, "bedrooms": 2, "displayAddress": "Flat 4, Mock Mill, Salford", "propertySubType": "Flat",
     "summary": "Two bed flat, leasehold 110 years remaining, requires updating.",
     "price": {"amount": 70000}, "propertyUrl": "/properties/92002",
     "location": {"latitude": 53.4875, "longitude": -2.2901}},
    {"id": 92003, "bedrooms": 3, "displayAddress": "9 Border Row, Cardiff CF10 1AA", "propertySubType": "Terraced",
     "summary": "Terrace for renovation.", "price": {"amount": 90000}, "propertyUrl": "/properties/92003"},
]
RM_REGION_COMMERCIAL = [
    {"id": 93001, "displayAddress": "Unit 2, Sample Parade, Leeds LS6 2AA", "propertySubType": "Retail",
     "summary": "Ground floor retail unit, 900 sq ft.", "price": {"amount": 120000}, "propertyUrl": "/properties/93001"},
]
RM_DISTRICT_RENT = [
    {"id": 94000 + i, "bedrooms": b, "displayAddress": f"{i} Let Street, Manchester M14", "propertySubType": "Terraced",
     "price": {"amount": r, "frequency": "monthly"}, "propertyUrl": f"/properties/{94000 + i}"}
    for i, (b, r) in enumerate([(3, 1250), (3, 1300), (3, 1350), (2, 1000), (2, 1050), (2, 1100)])
]

ZP_SALE_CARDS = [
    ("/for-sale/details/70001/", "£95,000\nGuide price\n2 bed terraced house\n5 Card Close, Chatham ME4 5YY\n"
                                  "Requires complete renovation. Cash buyers only."),
    ("/for-sale/details/70002/", "£112,000\n3 bed terraced house\n14 Mock Road, Chatham ME4 6XX\n"
                                  "Needs modernisation."),  # same house as Rightmove 90001
]
ZP_RENT_CARDS = [
    (f"/to-rent/details/7100{i}/", f"£{p:,} pcm\n{b} bed terraced house\n{i} Let Lane, Chatham")
    for i, (b, p) in enumerate([(3, 1500), (3, 1575), (2, 1250)])
]


def _page(title: str, body: str, head: str = "") -> bytes:
    return (f"<!doctype html><html><head><title>{title}</title>{head}</head>"
            f"<body>{body}</body></html>").encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, data: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p.startswith("/rm/typeahead"):
            name = q.get("query", "")
            if name[:1].isdigit() or (len(name) <= 4 and any(ch.isdigit() for ch in name)):
                body = {"matches": [{"id": "1652", "type": "OUTCODE", "displayName": name}]}
            else:
                body = {"matches": [{"id": "79192", "type": "REGION", "displayName": name}]}
            return self._send(200, json.dumps(body).encode(), "application/json")
        if p.startswith("/rm/commercial-property-for-sale/find.html"):
            props = RM_REGION_COMMERCIAL if int(q.get("index", 0)) == 0 else []
            nd = {"props": {"pageProps": {"searchResults": {"properties": props}}}}
            links = "".join(f'<div><a href="{x["propertyUrl"]}">£{x["price"]["amount"]:,}</a></div>' for x in props)
            head = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'
            return self._send(200, _page("Commercial search", links or "<p>No results</p>", head))
        if p.startswith("/rm/property-for-sale/find.html") or p.startswith("/rm/property-to-rent/find.html"):
            rent = "to-rent" in p
            region = q.get("locationIdentifier", "").startswith("REGION")
            if region:
                props = RM_REGION_SALE
            elif rent and Handler.district_rent:
                props = RM_DISTRICT_RENT
            else:
                props = RM_RENT if rent else RM_SALE
            props = props if int(q.get("index", 0)) == 0 else []
            nd = {"props": {"pageProps": {"searchResults": {"properties": props, "resultCount": len(props)}}}}
            links = "".join(f'<div class="card"><a href="{x["propertyUrl"]}">£{x["price"]["amount"]:,}</a>'
                            f'<span>{x["displayAddress"]}</span></div>' for x in props)
            head = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'
            return self._send(200, _page("Property search", links or "<p>No results</p>", head))
        if p.startswith("/rm/properties/") or p.startswith("/properties/"):
            pid = p.rstrip("/").split("/")[-1]
            model = RM_DETAIL.get(pid, {"propertyData": {"text": {"description": "Nice home."}}})
            head = f"<script>window.PAGE_MODEL = {json.dumps(model)}</script>"
            return self._send(200, _page("Property details", "<h1>Property</h1>", head))
        if p.startswith("/zp/for-sale/property/") or p.startswith("/zp/to-rent/property/"):
            cards = ZP_RENT_CARDS if "to-rent" in p else ZP_SALE_CARDS
            if int(q.get("pn", 1)) > 1:
                cards = []
            body = "".join(f'<article><a href="/zp{h}"><img alt=""></a><div>'
                           + "".join(f"<p>{ln}</p>" for ln in t.split("\n")) + "</div></article>"
                           for h, t in cards)
            return self._send(200, _page("Zoopla search", f"<button>Accept all</button>{body}"))
        if p.startswith("/zp/for-sale/details/"):
            return self._send(200, _page("Details", "<h2>Description</h2>\n<p>Two bedroom terrace requiring "
                                         "complete renovation throughout, no central heating, freehold, "
                                         "offered with no onward chain to cash buyers.</p>"))
        if p.startswith("/otm/"):
            return self._send(403, _page("Just a moment...", "<p>Checking your browser</p>"))
        return self._send(404, _page("Not found", "nope"))

    district_rent = False  # tests switch on district-level rentals for on-demand comps

    def do_POST(self):  # noqa: N802 — postcodes.io reverse geocoding stand-in
        if self.path.startswith("/pio/postcodes"):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = []
            for g in body.get("geolocations", []):
                hit = [{"outcode": "M5", "postcode": "M5 4WT"}] if abs(g["latitude"] - 53.4875) < 0.01 else []
                out.append({"query": g, "result": hit})
            return self._send(200, json.dumps({"status": 200, "result": out}).encode(), "application/json")
        return self._send(404, b"{}", "application/json")


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def portal_config(base: str) -> dict:
    return {
        "enabled": ["rightmove", "zoopla", "onthemarket"],
        "headless": True, "browser_channel": None,
        "max_sale_pages_per_outcode": 2, "max_rent_pages_per_outcode": 1, "rent_refresh_days": 7,
        "page_delay_seconds": [0, 0], "max_detail_pages_per_run": 10, "stop_after_seen_streak": 12,
        "base_urls": {"rightmove": f"{base}/rm", "rightmove_typeahead": f"{base}/rm/typeahead",
                      "zoopla": f"{base}/zp", "onthemarket": f"{base}/otm"},
        "rightmove_location_ids": {},
        "area_urls": {
            "rightmove": {
                "residential": "{base}/property-for-sale/find.html?locationIdentifier={loc}&maxPrice={max_price}&sortType=6&index={index}&maxDaysSinceAdded={days}",
                "commercial": "{base}/commercial-property-for-sale/find.html?locationIdentifier={loc}&maxPrice={max_price}&sortType=6&index={index}",
            },
        },
        "comps_max_districts_per_run": 10, "comps_rent_cache_days": 30, "comps_sale_cache_days": 14,
    }


if __name__ == "__main__":
    srv, url = start(8790)
    print("mock portals at", url)
    srv.serve_forever()
