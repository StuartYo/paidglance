"""Read-only TON client: incoming transfers for one address.

Uses public endpoints only (no keys, no signing):
  toncenter v3  /jetton/transfers, /transactions
  tonapi v2     get_wallet_address on Tether's master (canonical wallet check)
Set TONCENTER_API_KEY / TONAPI_KEY to lift the free rate limit (optional).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request

from .verify import USDT_MASTER, Transfer

TONCENTER = os.environ.get("TONCENTER_URL", "https://toncenter.com/api/v3")
TONAPI = os.environ.get("TONAPI_URL", "https://tonapi.io/v2")
UA = "PaidGlance/0.1 (+https://github.com/)"

_lock = threading.Lock()
_last_call = {"toncenter": 0.0, "tonapi": 0.0}
_MIN_GAP = 1.1  # free tier: ~1 request per second per service


def _get(service: str, url: str, timeout: float = 15.0) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if service == "toncenter" and os.environ.get("TONCENTER_API_KEY"):
        headers["X-API-Key"] = os.environ["TONCENTER_API_KEY"]
    if service == "tonapi" and os.environ.get("TONAPI_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["TONAPI_KEY"]
    with _lock:
        wait = _last_call[service] + _MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[service] = time.time()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # 429 → back off and retry
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    return {}


def _comment(payload) -> str:
    if isinstance(payload, dict):
        return (payload.get("comment") or payload.get("text") or "").strip()
    return ""


def _decimals(info: dict) -> int:
    raw = str((info.get("extra") or {}).get("decimals") or "6")
    return int(raw) if raw.isdigit() else 6


def jetton_transfers_in(address: str, limit: int = 20, master: str | None = None) -> list[Transfer]:
    q = {"owner_address": address, "direction": "in", "limit": limit, "sort": "desc"}
    if master:
        q["jetton_master"] = master
    d = _get("toncenter", f"{TONCENTER}/jetton/transfers?{urllib.parse.urlencode(q)}")
    meta = d.get("metadata") or {}
    out = []
    for t in d.get("jetton_transfers", []):
        m = t.get("jetton_master", "")
        info = ((meta.get(m) or {}).get("token_info") or [{}])[0]
        out.append(Transfer(
            tx_hash=t.get("transaction_hash", ""),
            time=int(t.get("transaction_now") or 0),
            kind="jetton",
            amount_raw=int(t.get("amount") or 0),
            comment=_comment(t.get("decoded_forward_payload")),
            sender=t.get("source", ""),
            jetton_master=m.lower(),
            symbol=info.get("symbol") or "",
            decimals=_decimals(info),
            source_wallet=(t.get("source_wallet") or "").lower(),
            aborted=bool(t.get("transaction_aborted")),
        ))
    return out


def jetton_transfers_of_master(master: str, limit: int = 20) -> list[Transfer]:
    """All recent transfers of one jetton (used by the evaluation)."""
    q = {"jetton_master": master, "limit": limit, "sort": "desc"}
    d = _get("toncenter", f"{TONCENTER}/jetton/transfers?{urllib.parse.urlencode(q)}")
    meta = d.get("metadata") or {}
    out = []
    for t in d.get("jetton_transfers", []):
        info = ((meta.get(t.get("jetton_master", "")) or {}).get("token_info") or [{}])[0]
        out.append(Transfer(
            tx_hash=t.get("transaction_hash", ""), time=int(t.get("transaction_now") or 0),
            kind="jetton", amount_raw=int(t.get("amount") or 0),
            comment=_comment(t.get("decoded_forward_payload")), sender=t.get("source", ""),
            jetton_master=(t.get("jetton_master") or "").lower(), symbol=info.get("symbol") or "",
            decimals=_decimals(info),
            source_wallet=(t.get("source_wallet") or "").lower(),
            aborted=bool(t.get("transaction_aborted")),
        ))
    return out


def ton_transfers_in(address: str, limit: int = 20) -> list[Transfer]:
    """Plain GRAM (TON) transfers that carry a text comment."""
    q = {"account": address, "limit": limit, "sort": "desc"}
    d = _get("toncenter", f"{TONCENTER}/transactions?{urllib.parse.urlencode(q)}")
    out = []
    for tx in d.get("transactions", []):
        m = tx.get("in_msg") or {}
        if not m.get("source"):
            continue  # external message (the wallet itself sending), not a payment in
        decoded = (m.get("message_content") or {}).get("decoded") or {}
        if decoded.get("type") != "text_comment":
            continue  # jetton notifications etc. are covered by jetton_transfers_in
        desc = tx.get("description") or {}
        aborted = bool(desc.get("aborted")) or (desc.get("compute_ph") or {}).get("success") is False
        out.append(Transfer(
            tx_hash=tx.get("hash", ""), time=int(tx.get("now") or 0), kind="ton",
            amount_raw=int(m.get("value") or 0), comment=_comment(decoded),
            sender=m.get("source", ""), aborted=aborted,
        ))
    return out


_canon_cache: dict[str, str] = {}


def canonical_usdt_wallet(owner: str) -> str | None:
    """Ask Tether's master which jetton wallet belongs to `owner`.

    Returns None when the lookup fails (network, rate limit, empty answer).
    verify() turns None into UNVERIFIED, never PAID. Failures are not cached,
    so the next poll asks again."""
    key = owner.lower()
    if key in _canon_cache:
        return _canon_cache[key]
    url = f"{TONAPI}/blockchain/accounts/{USDT_MASTER}/methods/get_wallet_address?args={urllib.parse.quote(owner)}"
    try:
        d = _get("tonapi", url)
        w = ((d.get("decoded") or {}).get("jetton_wallet_address") or "").lower()
    except Exception:
        return None
    if w:
        _canon_cache[key] = w
    return w or None


def friendly_tail(raw: str, n: int = 4) -> str:
    """Short, stable label for an address on a 576 px display."""
    h = raw.split(":")[-1]
    return "…" + h[-n:].upper() if h else "?"
