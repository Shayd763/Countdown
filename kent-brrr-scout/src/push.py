"""Web Push (RFC 8291 aes128gcm payload encryption + RFC 8292 VAPID), using `cryptography`.

Implemented directly rather than via pywebpush to avoid its source-only `http-ece`
dependency, which often fails to build on Windows/macOS.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import PROJECT_ROOT
from .storage import Storage

log = logging.getLogger(__name__)

VAPID_KEY_PATH = PROJECT_ROOT / "data" / "vapid_private.pem"
RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _pub_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------------------------------------------------------------------------
# VAPID
# ---------------------------------------------------------------------------


def load_vapid_key(path: Path = VAPID_KEY_PATH) -> ec.EllipticCurvePrivateKey:
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def vapid_public_key(key: ec.EllipticCurvePrivateKey | None = None) -> str:
    """base64url uncompressed public key — the browser's `applicationServerKey`."""
    return b64u(_pub_bytes((key or load_vapid_key()).public_key()))


def vapid_header(endpoint: str, key: ec.EllipticCurvePrivateKey, subject: str) -> str:
    u = urlparse(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                              "sub": subject}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={header}.{claims}.{b64u(sig)}, k={vapid_public_key(key)}"


# ---------------------------------------------------------------------------
# RFC 8291 payload encryption
# ---------------------------------------------------------------------------


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            sender_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError("push payload too large")
    as_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _pub_bytes(as_key.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = as_key.exchange(ec.ECDH(), ua_key)

    prk_key = _hmac(auth_secret, ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


def decrypt(body: bytes, ua_key: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """Receiver side — used by tests to prove `encrypt` is correct."""
    salt, _rs, idlen = body[:16], *struct.unpack("!IB", body[16:21])
    as_public = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    ua_public = _pub_bytes(ua_key.public_key())
    as_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    ecdh_secret = ua_key.exchange(ec.ECDH(), as_key)
    prk_key = _hmac(b64u_decode(auth), ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    return padded.rstrip(b"\x00")[:-1]  # strip padding delimiter 0x02


# ---------------------------------------------------------------------------
# Sending + subscription storage
# ---------------------------------------------------------------------------


def subject() -> str:
    return os.environ.get("VAPID_SUBJECT") or "mailto:brrr-scout@example.com"


def send(sub: dict[str, Any], message: dict[str, Any], *, ttl: int = 86400,
         key: ec.EllipticCurvePrivateKey | None = None, client: httpx.Client | None = None) -> int:
    """Encrypt and POST one notification. Returns the push service's HTTP status."""
    key = key or load_vapid_key()
    body = encrypt(json.dumps(message, separators=(",", ":")).encode(), sub["p256dh"], sub["auth"])
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
               "TTL": str(ttl), "Urgency": "normal",
               "Authorization": vapid_header(sub["endpoint"], key, subject())}
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        return client.post(sub["endpoint"], content=body, headers=headers).status_code
    finally:
        if own:
            client.close()


def save_subscription(st: Storage, user_id: int, sub: dict[str, Any], user_agent: str = "") -> None:
    keys = sub.get("keys") or {}
    if not sub.get("endpoint", "").startswith("https://") and not os.environ.get("BRRR_ALLOW_HTTP_PUSH"):
        raise ValueError("invalid push endpoint")
    st.db["push_subs"].upsert({
        "endpoint": sub["endpoint"], "user_id": user_id, "p256dh": keys["p256dh"], "auth": keys["auth"],
        "user_agent": user_agent[:200], "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }, pk="endpoint")


def remove_subscription(st: Storage, endpoint: str) -> None:
    st.db.execute("delete from push_subs where endpoint = ?", [endpoint])


def user_subscriptions(st: Storage, user_id: int) -> list[dict[str, Any]]:
    return list(st.db["push_subs"].rows_where("user_id = ?", [user_id]))


def send_to_user(st: Storage, user_id: int, message: dict[str, Any]) -> dict[str, int]:
    """Send to all of a user's devices; prunes subscriptions the push service says are gone."""
    out = {"sent": 0, "failed": 0, "removed": 0}
    subs = user_subscriptions(st, user_id)
    if not subs:
        return out
    key = load_vapid_key()
    with httpx.Client(timeout=20) as client:
        for sub in subs:
            try:
                code = send(sub, message, key=key, client=client)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("push failed: %s", exc)
                out["failed"] += 1
                continue
            if code in (200, 201, 202):
                out["sent"] += 1
                st.db["push_subs"].update(sub["endpoint"], {
                    "last_ok": datetime.now(UTC).isoformat(timespec="seconds")})
            elif code in (404, 410):
                remove_subscription(st, sub["endpoint"])
                out["removed"] += 1
            else:
                log.warning("push service returned %s for user %s", code, user_id)
                out["failed"] += 1
    return out
