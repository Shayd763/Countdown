"""Sign-in, profile/preferences, push subscriptions, digest, phone access, and the installable app."""

from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from .auth import COOKIE, SESSION_DAYS, Auth, LoginLimiter
from .storage import Storage

STATIC = Path(__file__).parent / "static"
APP_DIR = STATIC / "app"

# API paths reachable without signing in.
PUBLIC_API = {"/api/auth/status", "/api/auth/login", "/api/auth/setup"}


class Credentials(BaseModel):
    email: str
    password: str
    name: str | None = None


class ProfileUpdate(BaseModel):
    name: str | None = None
    prefs: dict[str, Any] | None = None
    password: str | None = None
    current_password: str | None = None


class PushSub(BaseModel):
    endpoint: str
    keys: dict[str, str]


class NewUser(BaseModel):
    email: str
    password: str
    name: str | None = None
    is_admin: bool = False


def current_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user["is_admin"]:
        raise HTTPException(403, "Only the owner account can change this")
    return user


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _set_cookie(resp: Response, request: Request, token: str) -> None:
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=_is_https(request), path="/")


# ---------------------------------------------------------------------------
# Tailscale (private HTTPS access from the phone)
# ---------------------------------------------------------------------------


def tailscale_bin() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    for p in ("/Applications/Tailscale.app/Contents/MacOS/Tailscale",
              r"C:\Program Files\Tailscale\tailscale.exe"):
        if Path(p).exists():
            return p
    return None


def tailscale_info(port: int) -> dict[str, Any]:
    import json

    ts = tailscale_bin()
    info: dict[str, Any] = {"installed": bool(ts), "logged_in": False, "dns_name": None,
                            "serving": False, "url": None, "message": ""}
    if not ts:
        info["message"] = "Tailscale isn't installed on this laptop."
        return info
    try:
        st = json.loads(subprocess.run([ts, "status", "--json"], capture_output=True, text=True,
                                       timeout=10).stdout or "{}")
        self_node = st.get("Self") or {}
        dns = (self_node.get("DNSName") or "").rstrip(".")
        info["logged_in"] = st.get("BackendState") == "Running" and bool(dns)
        info["dns_name"] = dns or None
        if dns:
            info["url"] = f"https://{dns}/app/"
        serve = subprocess.run([ts, "serve", "status", "--json"], capture_output=True, text=True, timeout=10)
        cfg = json.loads(serve.stdout or "{}") if serve.returncode == 0 else {}
        info["serving"] = str(port) in json.dumps(cfg)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        info["message"] = f"Couldn't read Tailscale status: {exc}"
    return info


def tailscale_enable(port: int) -> dict[str, Any]:
    ts = tailscale_bin()
    if not ts:
        raise HTTPException(400, "Install Tailscale on this laptop first (tailscale.com/download)")
    import re

    def _text(v: Any) -> str:
        return v.decode("utf-8", "replace") if isinstance(v, bytes) else (v or "")

    try:
        r = subprocess.run([ts, "serve", "--bg", str(port)], capture_output=True, text=True, timeout=20)
        out, ok = (r.stdout + "\n" + r.stderr).strip(), r.returncode == 0
    except subprocess.TimeoutExpired as exc:
        # First time on a tailnet, `serve` (or HTTPS certificates) must be approved in the browser,
        # and the CLI waits for that forever. Hand the approval link to the user instead.
        out, ok = (_text(exc.stdout) + "\n" + _text(exc.stderr)).strip(), False
    link = re.search(r"https://login\.tailscale\.com/\S+", out)
    if link:
        return {"output": out, "approve_url": link.group(0), **tailscale_info(port)}
    if not ok:
        raise HTTPException(500, out or "tailscale serve failed")
    return {"output": out, **tailscale_info(port)}


def qr_svg(text: str) -> bytes:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def install_account_routes(app: FastAPI, storage: Callable[[], Storage]) -> None:
    limiter = LoginLimiter()

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        request.state.user = None
        token = request.cookies.get(COOKIE)
        if token:
            request.state.user = Auth(storage()).user_for_token(token)
        if path.startswith("/api/") and path not in PUBLIC_API and not request.state.user:
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        return await call_next(request)

    # -- installable app -------------------------------------------------------

    @app.get("/app")
    def app_redirect() -> Response:
        return Response(status_code=307, headers={"Location": "/app/"})

    @app.get("/app/")
    def app_index() -> FileResponse:
        return FileResponse(APP_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        return FileResponse(APP_DIR / "sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        return FileResponse(APP_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/icons/{name}")
    def icons(name: str) -> FileResponse:
        p = (APP_DIR / "icons" / name).resolve()
        if p.parent != (APP_DIR / "icons").resolve() or not p.exists():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "public, max-age=604800"})

    # -- auth ------------------------------------------------------------------

    @app.get("/api/auth/status")
    def auth_status(request: Request) -> dict[str, Any]:
        return {"setup_needed": Auth(storage()).user_count() == 0, "user": request.state.user}

    @app.post("/api/auth/setup")
    def auth_setup(body: Credentials, request: Request) -> Response:
        a = Auth(storage())
        if a.user_count() > 0:
            raise HTTPException(403, "An owner account already exists — sign in instead")
        try:
            user = a.create_user(body.email, body.name or "", body.password, is_admin=True)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        resp = JSONResponse({"user": user})
        _set_cookie(resp, request, a.new_session(user["id"], request.headers.get("user-agent", "")))
        return resp

    @app.post("/api/auth/login")
    def auth_login(body: Credentials, request: Request) -> Response:
        key = f"{request.client.host if request.client else '?'}|{body.email.lower()}"
        if limiter.blocked(key):
            raise HTTPException(429, "Too many attempts — wait 15 minutes")
        a = Auth(storage())
        user = a.authenticate(body.email, body.password)
        if not user:
            limiter.fail(key)
            raise HTTPException(401, "Wrong email or password")
        limiter.reset(key)
        resp = JSONResponse({"user": user})
        _set_cookie(resp, request, a.new_session(user["id"], request.headers.get("user-agent", "")))
        return resp

    @app.post("/api/auth/logout")
    def auth_logout(request: Request) -> Response:
        Auth(storage()).end_session(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/auth/me")
    def me(request: Request) -> dict[str, Any]:
        return current_user(request)

    @app.put("/api/auth/me")
    def update_me(body: ProfileUpdate, request: Request) -> dict[str, Any]:
        user = current_user(request)
        try:
            return Auth(storage()).update_user(user["id"], name=body.name, prefs=body.prefs,
                                               password=body.password,
                                               current_password=body.current_password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/users")
    def users(request: Request) -> list[dict[str, Any]]:
        require_admin(request)
        return Auth(storage()).list_users()

    @app.post("/api/users")
    def add_user(body: NewUser, request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            return Auth(storage()).create_user(body.email, body.name or "", body.password, body.is_admin)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/users/{user_id}")
    def del_user(user_id: int, request: Request) -> dict[str, Any]:
        me_ = require_admin(request)
        if user_id == me_["id"]:
            raise HTTPException(400, "You can't delete your own account")
        Auth(storage()).delete_user(user_id)
        return {"ok": True}

    # -- digest & push -------------------------------------------------------------

    @app.get("/api/digest")
    def digest(request: Request) -> dict[str, Any]:
        from .digest import build_digest

        return build_digest(storage(), current_user(request)["prefs"])

    @app.get("/api/push/key")
    def push_key() -> dict[str, Any]:
        from .push import vapid_public_key

        return {"key": vapid_public_key()}

    @app.post("/api/push/subscribe")
    def push_subscribe(body: PushSub, request: Request) -> dict[str, Any]:
        from .push import save_subscription

        try:
            save_subscription(storage(), current_user(request)["id"], body.model_dump(),
                              request.headers.get("user-agent", ""))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, f"Bad subscription: {exc}") from exc
        return {"ok": True}

    @app.post("/api/push/unsubscribe")
    def push_unsubscribe(body: dict[str, Any], request: Request) -> dict[str, Any]:
        from .push import remove_subscription

        current_user(request)
        remove_subscription(storage(), str(body.get("endpoint", "")))
        return {"ok": True}

    @app.get("/api/push/devices")
    def push_devices(request: Request) -> list[dict[str, Any]]:
        from .push import user_subscriptions

        return [{"endpoint": s["endpoint"][:60] + "…", "user_agent": s["user_agent"],
                 "created_at": s["created_at"], "last_ok": s["last_ok"]}
                for s in user_subscriptions(storage(), current_user(request)["id"])]

    @app.post("/api/push/test")
    def push_test(request: Request) -> dict[str, Any]:
        from .digest import build_digest, notification_for
        from .push import send_to_user

        user = current_user(request)
        st = storage()
        msg = notification_for(build_digest(st, user["prefs"]), user["name"])
        res = send_to_user(st, user["id"], msg)
        if not res["sent"]:
            raise HTTPException(400, "No device received it — turn on notifications in the app first"
                                if not res["failed"] else "The push service rejected it (see Logs)")
        return {"message": f"Sent to {res['sent']} device(s)", **res}

    # -- phone access ------------------------------------------------------------

    def _port() -> int:
        return int(os.environ.get("BRRR_PORT", "8000"))

    @app.get("/api/phone")
    def phone(request: Request) -> dict[str, Any]:
        current_user(request)
        return {"platform": platform.system(), "port": _port(), "tailscale": tailscale_info(_port())}

    @app.post("/api/phone/enable")
    def phone_enable(request: Request) -> dict[str, Any]:
        require_admin(request)
        return tailscale_enable(_port())

    @app.get("/api/phone/qr.svg")
    def phone_qr(url: str, request: Request) -> Response:
        current_user(request)
        if not url.startswith("https://") or len(url) > 300:
            raise HTTPException(400, "bad url")
        return Response(qr_svg(url), media_type="image/svg+xml")
