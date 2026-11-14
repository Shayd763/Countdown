"""Phone app backend: accounts, preferences, digest, Web Push delivery."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient

from src.push import _pub_bytes, b64u, b64u_decode, decrypt


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from src import config as config_mod
    from src import push as push_mod

    monkeypatch.setenv("BRRR_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("BRRR_ALLOW_HTTP_PUSH", "1")
    monkeypatch.setattr(config_mod, "USER_OVERRIDES", tmp_path / "user.yaml")
    monkeypatch.setattr(push_mod, "VAPID_KEY_PATH", tmp_path / "vapid.pem")
    from src import web
    return TestClient(web.create_app())


def _setup(c, email="owner@example.com", pw="correct-horse"):
    r = c.post("/api/auth/setup", json={"email": email, "password": pw, "name": "Owner Person"})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def _wait_job(c):
    for _ in range(300):
        j = c.get("/api/job").json()
        if j and not j["running"]:
            return j
        time.sleep(0.03)
    raise AssertionError("job stuck")


def test_auth_flow(client):
    c = client
    assert c.get("/api/deals").status_code == 401
    assert c.get("/app/").status_code == 200 and c.get("/sw.js").status_code == 200
    assert c.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert c.get("/icons/icon-192.png").status_code == 200
    assert c.get("/icons/..%2Fsw.js").status_code == 404
    _setup(c)
    assert c.post("/api/auth/setup", json={"email": "x@example.com", "password": "12345678"}).status_code == 403
    me = c.get("/api/auth/me").json()
    assert me["name"] == "Owner Person" and me["prefs"]["notify"] == "passes"
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/login", json={"email": "owner@example.com", "password": "nope-nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "OWNER@example.com", "password": "correct-horse"}).status_code == 200
    # preferences
    me = c.put("/api/auth/me", json={"prefs": {"min_roce": 15, "outcodes": ["ME4"], "bogus": 1}}).json()
    assert me["prefs"]["min_roce"] == 15 and "bogus" not in me["prefs"]
    # second person: not admin, can't change settings
    c.post("/api/users", json={"email": "partner@example.com", "password": "another-pass", "name": "P"})
    c.post("/api/auth/logout")
    c.post("/api/auth/login", json={"email": "partner@example.com", "password": "another-pass"})
    assert c.put("/api/settings", json={"config": {"thresholds": {"min_gross_yield": 0.5}}}).status_code == 403
    assert c.get("/api/users").status_code == 403


def test_login_rate_limit(client):
    _setup(client)
    client.post("/api/auth/logout")
    for _ in range(8):
        client.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong-pass"})
    r = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "correct-horse"})
    assert r.status_code == 429


def test_digest(client):
    c = client
    _setup(c)
    c.post("/api/scan", json={"sources": ["sample"], "use_claude": False})
    assert _wait_job(c)["error"] is None
    dg = c.get("/api/digest").json()
    assert dg["counts"]["passing"] == 3 and len(dg["best"]) == 3
    top = dg["best"][0]
    for k in ("cash_to_buy", "refi_loan", "money_left_in", "net_monthly_cashflow", "total_cash_deployed"):
        assert isinstance(top[k], float | int)
    assert top["cash_to_buy"] > top["asking_price"]
    for nm in dg["near_misses"]:  # every near miss genuinely passes at its max offer
        assert nm["max_offer_price"] < nm["asking_price"] and nm["at_offer_cashflow"] > 0
    # preferences narrow the digest
    c.put("/api/auth/me", json={"prefs": {"outcodes": ["ME4"]}})
    dg2 = c.get("/api/digest").json()
    assert {d["outcode"] for d in dg2["best"] + dg2["near_misses"]} <= {"ME4"}
    c.put("/api/auth/me", json={"prefs": {"outcodes": [], "max_cash_in": 1000}})
    assert c.get("/api/digest").json()["counts"]["passing"] == 0


class _PushSink(BaseHTTPRequestHandler):
    received: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _PushSink.received.append({"path": self.path, "headers": dict(self.headers), "body": body})
        code = 410 if self.path.endswith("/gone") else 201
        self.send_response(code)
        self.end_headers()


def _verify_vapid(auth_header: str, endpoint_origin: str):
    t = auth_header.split("t=")[1].split(",")[0]
    k = auth_header.split("k=")[1].strip()
    head, claims, sig = t.split(".")
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64u_decode(k))
    raw = b64u_decode(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if bad
    c = json.loads(b64u_decode(claims))
    assert c["aud"] == endpoint_origin and c["exp"] > time.time() and c["sub"].startswith("mailto:")


def test_push_delivery_end_to_end(client):
    c = client
    _setup(c)
    c.post("/api/scan", json={"sources": ["sample"], "use_claude": False})
    _wait_job(c)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _PushSink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    _PushSink.received.clear()
    try:
        ua_key = ec.generate_private_key(ec.SECP256R1())
        auth = b64u(os.urandom(16))
        sub = {"endpoint": f"{base}/push/abc", "keys": {"p256dh": b64u(_pub_bytes(ua_key.public_key())),
                                                       "auth": auth}}
        assert c.post("/api/push/subscribe", json=sub).status_code == 200
        assert c.get("/api/push/key").json()["key"]
        r = c.post("/api/push/test")
        assert r.status_code == 200 and r.json()["sent"] == 1, r.text
        got = _PushSink.received[-1]
        assert got["headers"]["Content-Encoding"] == "aes128gcm" and int(got["headers"]["TTL"]) > 0
        _verify_vapid(got["headers"]["Authorization"], base)
        msg = json.loads(decrypt(got["body"], ua_key, auth))
        assert msg["title"] == "3 BRRR deals pass today" and "cash in" in msg["body"]
        assert msg["url"].startswith("/app/") and len(msg["digest"]["best"]) == 3

        # scheduled-run path: digest goes to subscribed users; expired endpoints are pruned
        gone = {"endpoint": f"{base}/push/gone", "keys": sub["keys"]}
        c.post("/api/push/subscribe", json=gone)
        from src.digest import send_daily_digests
        from src.storage import Storage

        res = send_daily_digests(Storage())
        assert res["owner@example.com"] == {"sent": 1, "failed": 0, "removed": 1}
        assert len(c.get("/api/push/devices").json()) == 1
    finally:
        srv.shutdown()


def test_https_only_push_endpoints(client, monkeypatch):
    _setup(client)
    monkeypatch.delenv("BRRR_ALLOW_HTTP_PUSH")
    bad = {"endpoint": "http://evil.test/x", "keys": {"p256dh": "a", "auth": "b"}}
    assert client.post("/api/push/subscribe", json=bad).status_code == 400


def test_phone_qr_requires_https(client):
    _setup(client)
    assert client.get("/api/phone/qr.svg", params={"url": "javascript:alert(1)"}).status_code == 400
    r = client.get("/api/phone/qr.svg", params={"url": "https://laptop.tailnet.ts.net/app/"})
    assert r.status_code == 200 and r.text.startswith("<?xml")
    assert client.get("/api/phone").json()["tailscale"]["installed"] in (True, False)


def test_push_encryption_matches_rfc8291_vector():
    """RFC 8291 Appendix A: proves interoperability with real browser push services."""
    from src.push import encrypt

    as_key = ec.derive_private_key(
        int.from_bytes(b64u_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = encrypt(b"When I grow up, I want to be a watermelon",
                   "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                   "BTBZMqHH6r4Tts7J_aSIgg", salt=b64u_decode("DGv6ra1nlYgDCS1FRnbzlw"), sender_key=as_key)
    assert b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
        "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
