"""Local user accounts: scrypt password hashes, opaque session tokens (only hashes stored)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlite_utils

from .storage import Storage

SESSION_DAYS = 90
COOKIE = "brrr_session"

DEFAULT_PREFS: dict[str, Any] = {
    "notify": "passes",        # "always" | "passes" (only when something passes) | "off"
    "only_new": False,         # digest counts only deals first seen in the latest run
    "min_roce": None,          # % — None = any
    "max_cash_in": None,       # £ total cash deployed — None = any
    "outcodes": [],            # [] = all target outcodes
    "categories": ["residential"],
    "digest_size": 5,
    "plan": None,              # finance plan id for this person's views and alerts; None = default
}


def _now() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        salt, want = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
    except ValueError:
        return False
    got = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=len(want))
    return hmac.compare_digest(got, want)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    prefs = {**DEFAULT_PREFS, **json.loads(row.get("prefs") or "{}")}
    return {"id": row["id"], "email": row["email"], "name": row["name"],
            "is_admin": bool(row["is_admin"]), "prefs": prefs}


class Auth:
    def __init__(self, storage: Storage):
        self.st = storage
        self.db = storage.db

    def user_count(self) -> int:
        return self.db["users"].count

    def create_user(self, email: str, name: str, password: str, is_admin: bool = False) -> dict:
        email = email.strip().lower()
        if "@" not in email or len(email) > 200:
            raise ValueError("Enter a valid email address")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        if list(self.db.query("select 1 from users where email = ?", [email])):
            raise ValueError("An account with that email already exists")
        uid = self.db["users"].insert({
            "email": email, "name": (name or email.split("@")[0]).strip()[:80],
            "pw_hash": hash_password(password), "is_admin": int(is_admin),
            "prefs": json.dumps(DEFAULT_PREFS), "created_at": _now().isoformat(timespec="seconds"),
        }).last_pk
        self.st.audit("user_created", None, {"user_id": uid})
        return public_user(self.db["users"].get(uid))

    def authenticate(self, email: str, password: str) -> dict | None:
        rows = list(self.db.query("select * from users where email = ?", [email.strip().lower()]))
        if not rows:
            hash_password(password)  # equalise timing
            return None
        return public_user(rows[0]) if verify_password(password, rows[0]["pw_hash"]) else None

    def new_session(self, user_id: int, user_agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        self.db["sessions"].insert({
            "token_hash": _token_hash(token), "user_id": user_id,
            "created_at": _now().isoformat(timespec="seconds"),
            "expires_at": (_now() + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"),
            "user_agent": user_agent[:200],
        })
        return token

    def user_for_token(self, token: str | None) -> dict | None:
        if not token:
            return None
        try:
            sess = self.db["sessions"].get(_token_hash(token))
        except sqlite_utils.db.NotFoundError:
            return None
        if sess["expires_at"] < _now().isoformat(timespec="seconds"):
            self.db["sessions"].delete(sess["token_hash"])
            return None
        try:
            return public_user(self.db["users"].get(sess["user_id"]))
        except sqlite_utils.db.NotFoundError:
            return None

    def end_session(self, token: str | None) -> None:
        if token:
            self.db.execute("delete from sessions where token_hash = ?", [_token_hash(token)])

    def update_user(self, user_id: int, *, name: str | None = None, prefs: dict | None = None,
                    password: str | None = None, current_password: str | None = None) -> dict:
        row = self.db["users"].get(user_id)
        upd: dict[str, Any] = {}
        if name is not None:
            upd["name"] = name.strip()[:80]
        if prefs is not None:
            merged = {**DEFAULT_PREFS, **json.loads(row["prefs"] or "{}")}
            merged.update({k: v for k, v in prefs.items() if k in DEFAULT_PREFS})
            upd["prefs"] = json.dumps(merged)
        if password is not None:
            if not current_password or not verify_password(current_password, row["pw_hash"]):
                raise ValueError("Current password is wrong")
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters")
            upd["pw_hash"] = hash_password(password)
            self.db.execute("delete from sessions where user_id = ?", [user_id])
        if upd:
            self.db["users"].update(user_id, upd)
        return public_user(self.db["users"].get(user_id))

    def list_users(self) -> list[dict]:
        return [public_user(r) for r in self.db["users"].rows]

    def delete_user(self, user_id: int) -> None:
        self.db.execute("delete from sessions where user_id = ?", [user_id])
        self.db.execute("delete from push_subs where user_id = ?", [user_id])
        self.db["users"].delete(user_id)


class LoginLimiter:
    """Simple in-memory brake on password guessing: 8 failures per 15 min per key."""

    def __init__(self, limit: int = 8, window: int = 900):
        self.limit, self.window = limit, window
        self.fails: dict[str, list[float]] = {}

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        self.fails[key] = [t for t in self.fails.get(key, []) if now - t < self.window]
        return len(self.fails[key]) >= self.limit

    def fail(self, key: str) -> None:
        self.fails.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        self.fails.pop(key, None)
