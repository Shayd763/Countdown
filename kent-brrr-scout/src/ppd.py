"""HM Land Registry Price Paid Data, stored locally for sold-price comps anywhere in England.

Contains HM Land Registry data © Crown copyright and database right, licensed under the
Open Government Licence v3.0.

Files (no header row), one transaction per line:
  0 id, 1 price, 2 date, 3 postcode, 4 type (D/S/T/F/O), 5 new build (Y/N), 6 tenure (F/L),
  7 PAON, 8 SAON, 9 street, 10 locality, 11 town, 12 district, 13 county,
  14 category (A standard / B additional), 15 record status (A add, C change, D delete)
"""

from __future__ import annotations

import csv
import io
import logging
import statistics
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from .storage import Storage

log = logging.getLogger(__name__)

TYPE_CODE = {"detached": "D", "bungalow": "D", "semi-detached": "S", "terraced": "T", "end-terrace": "T",
             "flat": "F", "maisonette": "F"}
BATCH = 5000


def ensure_table(st: Storage) -> None:
    if "ppd" not in st.db.table_names():
        st.db["ppd"].create({"tid": str, "price": int, "date": str, "postcode": str, "outcode": str,
                             "ptype": str, "new_build": int, "tenure": str}, pk="tid")
        st.db["ppd"].create_index(["outcode", "ptype", "date"])


def _rows(lines: Iterable[str]) -> Iterator[list[str]]:
    yield from csv.reader(lines)


def apply_rows(st: Storage, rows: Iterable[list[str]], *, standard_only: bool = True) -> dict[str, int]:
    """Insert/replace (status A or C) and delete (D). Category B (repossessions, transfers
    to companies, etc.) is skipped by default because it distorts open-market comps."""
    ensure_table(st)
    counts = {"added": 0, "deleted": 0, "skipped": 0}
    batch: list[tuple] = []
    deletes: list[tuple] = []
    conn = st.db.conn

    def flush() -> None:
        if batch:
            conn.executemany("insert or replace into ppd (tid, price, date, postcode, outcode, ptype, "
                             "new_build, tenure) values (?, ?, ?, ?, ?, ?, ?, ?)", batch)
            batch.clear()
        if deletes:
            conn.executemany("delete from ppd where tid = ?", deletes)
            deletes.clear()
        conn.commit()

    for r in rows:
        if len(r) < 15:
            counts["skipped"] += 1
            continue
        tid = r[0].strip("{}")
        category = r[14].strip() if len(r) > 14 else "A"
        status = (r[15].strip() if len(r) > 15 else "") or "A"
        if status == "D":
            deletes.append((tid,))
            counts["deleted"] += 1
        else:
            postcode = r[3].strip().upper()
            if not postcode or (standard_only and category == "B"):
                counts["skipped"] += 1
                continue
            try:
                price = int(float(r[1]))
            except ValueError:
                counts["skipped"] += 1
                continue
            batch.append((tid, price, r[2][:10], postcode, postcode.split(" ")[0], r[4].strip(),
                          int(r[5].strip() == "Y"), r[6].strip()))
            counts["added"] += 1
        if len(batch) >= BATCH or len(deletes) >= BATCH:
            flush()
    flush()
    return counts


def _stream_lines(url: str, progress: Callable[[str], None] | None = None) -> Iterator[str]:
    with httpx.stream("GET", url, timeout=httpx.Timeout(60, read=300), follow_redirects=True) as r:
        r.raise_for_status()
        for n, line in enumerate(r.iter_lines(), 1):
            if progress and n % 200_000 == 0:
                progress(f"Land Registry: {n:,} sales read from {url.rsplit('/', 1)[-1]}")
            yield line


def refresh(st: Storage, cfg: dict[str, Any], progress: Callable[..., None] | None = None,
            force: bool = False) -> dict[str, Any]:
    """Download what's needed: yearly files when the table is empty, else the monthly update."""
    pc = cfg.get("land_registry_ppd") or {}
    if not pc.get("enabled", True):
        return {"skipped": "disabled"}
    ensure_table(st)
    last = st.kv_get("ppd:refreshed_at")
    fresh = last and datetime.fromisoformat(last) > datetime.now(UTC) - timedelta(days=int(pc.get("refresh_days", 30)))
    if fresh and not force:
        return {"skipped": "fresh", "rows": st.db["ppd"].count}
    base = pc.get("base_url", "").rstrip("/")
    say = (lambda m: progress("comps", m)) if progress else None
    totals = {"added": 0, "deleted": 0, "skipped": 0}
    files: list[str]
    if st.db["ppd"].count == 0:
        this_year = date.today().year
        files = [f"pp-{y}.csv" for y in range(this_year - int(pc.get("years", 2)) + 1, this_year + 1)]
    else:
        files = [pc.get("monthly_file", "pp-monthly-update-new-version.csv")]
    for name in files:
        url = f"{base}/{name}"
        if say:
            say(f"Land Registry: downloading {name}")
        try:
            c = apply_rows(st, _rows(_stream_lines(url, say)))
        except httpx.HTTPError as exc:
            log.warning("Land Registry download %s failed: %s", url, exc)
            totals["error"] = f"{name}: {exc}"
            continue
        for k in ("added", "deleted", "skipped"):
            totals[k] += c[k]
    # keep only the lookback window
    months = int((cfg.get("local_comps") or {}).get("sold_lookback_months", 24)) + 1
    cutoff = (date.today() - timedelta(days=31 * months)).isoformat()
    st.db.execute("delete from ppd where date < ?", [cutoff])
    st.db.conn.commit()
    if "error" not in totals or totals["added"]:
        st.kv_set("ppd:refreshed_at", datetime.now(UTC).isoformat(timespec="seconds"))
    totals["rows"] = st.db["ppd"].count
    st.audit("ppd_refresh", None, totals)
    return totals


def sold_prices(st: Storage, outcode: str, ptype: str | None, months: int = 24,
                include_new_build: bool = False) -> list[float]:
    if "ppd" not in st.db.table_names():
        return []
    since = (date.today() - timedelta(days=31 * months)).isoformat()
    where, params = ["outcode = ?", "date >= ?"], [outcode.upper(), since]
    code = TYPE_CODE.get(ptype or "")
    if code:
        where.append("ptype = ?")
        params.append(code)
    if not include_new_build:
        where.append("new_build = 0")
    return [float(r["price"]) for r in st.db.query(f"select price from ppd where {' and '.join(where)}", params)]


def summary(st: Storage) -> dict[str, Any]:
    if "ppd" not in st.db.table_names() or not st.db["ppd"].count:
        return {"rows": 0}
    r = next(st.db.query("select count(*) n, min(date) first, max(date) last, count(distinct outcode) d from ppd"))
    return {"rows": r["n"], "from": r["first"], "to": r["last"], "districts": r["d"],
            "refreshed_at": st.kv_get("ppd:refreshed_at")}


def median_sold(st: Storage, outcode: str, ptype: str | None, months: int = 24) -> float | None:
    p = sold_prices(st, outcode, ptype, months)
    return statistics.median(p) if len(p) >= 3 else None


def parse_csv_text(text: str) -> Iterator[list[str]]:
    """For tests and small local files."""
    return _rows(io.StringIO(text))
