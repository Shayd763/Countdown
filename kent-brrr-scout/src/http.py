"""HTTP helpers: a PropertyData client with retries, and an offline fixture client."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import PROJECT_ROOT

log = logging.getLogger(__name__)

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures"


class PropertyDataError(RuntimeError):
    pass


class DataClient(Protocol):
    def get(self, endpoint: str, **params: Any) -> dict[str, Any]: ...


def get_with_retry(
    client: httpx.Client, url: str, *, params: dict | None = None, attempts: int = 4, **kw: Any
) -> httpx.Response:
    delay = 1.0
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            resp = client.get(url, params=params, **kw)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if i < attempts - 1:
                time.sleep(delay)
                delay *= 2
    raise PropertyDataError(f"GET {url} failed after {attempts} attempts: {last_exc}")


class PropertyDataClient:
    """Thin wrapper over https://api.propertydata.co.uk (key passed as query param)."""

    def __init__(self, api_key: str | None = None, base_url: str = "https://api.propertydata.co.uk",
                 delay: float = 0.4, timeout: float = 30.0):
        self.api_key = api_key or os.environ.get("PROPERTYDATA_API_KEY")
        if not self.api_key:
            raise PropertyDataError("PROPERTYDATA_API_KEY not set (use --offline for fixtures)")
        self.base_url = base_url.rstrip("/")
        self.delay = delay
        self._http = httpx.Client(timeout=timeout)
        self._cache: dict[str, dict[str, Any]] = {}

    # Params our enrichment passes that a given PropertyData endpoint doesn't take.
    _UNSUPPORTED = {"sold-prices": {"bedrooms"}, "sourced-properties": {"bedrooms"}}

    def get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        drop = self._UNSUPPORTED.get(endpoint.strip("/"), set())
        params = {k: v for k, v in params.items() if v is not None and k not in drop}
        cache_key = endpoint + json.dumps(params, sort_keys=True)
        if cache_key in self._cache:
            return self._cache[cache_key]
        resp = get_with_retry(
            self._http, f"{self.base_url}/{endpoint.strip('/')}", params={"key": self.api_key, **params}
        )
        try:
            data = resp.json()
        except ValueError as exc:
            raise PropertyDataError(f"{endpoint}: non-JSON response ({resp.status_code})") from exc
        if resp.status_code >= 400 or data.get("status") == "error":
            raise PropertyDataError(f"{endpoint}: {data.get('message') or resp.status_code}")
        time.sleep(self.delay)
        self._cache[cache_key] = data
        return data


class FixtureClient:
    """Serves canned PropertyData payloads from tests/fixtures for offline runs & tests.

    Fixture files are named after the endpoint (``sourced-properties`` ->
    ``sourced_properties.json``). Files whose top level is keyed by outcode return
    that outcode's payload (falling back to ``default``).
    """

    def __init__(self, fixture_dir: Path = FIXTURE_DIR):
        self.fixture_dir = fixture_dir

    def get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        path = self.fixture_dir / f"{endpoint.strip('/').replace('-', '_')}.json"
        if not path.exists():
            raise PropertyDataError(f"no fixture for {endpoint}")
        data = json.loads(path.read_text(encoding="utf-8"))
        outcode = str(params.get("postcode", "")).split(" ")[0].upper()
        if endpoint.strip("/") == "sourced-properties":
            wanted_list = params.get("list")
            props = [
                pr for pr in data["properties"]
                if pr["postcode"].upper().startswith(outcode + " ")
                and (wanted_list is None or pr.get("_list", wanted_list) == wanted_list)
            ]
            return {"status": "success", "properties": props}
        if "by_outcode" in data:
            data = data["by_outcode"].get(outcode) or data["by_outcode"]["default"]
        if "by_type" in data:
            data = data["by_type"].get(str(params.get("type"))) or data["by_type"]["default"]
        if "by_bedrooms" in data:
            beds = str(params.get("bedrooms"))
            data = data["by_bedrooms"].get(beds) or data["by_bedrooms"]["default"]
        raw = data.get("data", {}).get("raw_data")
        if raw and params.get("type"):
            data = {**data, "data": {**data["data"],
                                     "raw_data": [r for r in raw if r.get("type") == params["type"]]}}
        return data
