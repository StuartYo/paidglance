"""Shift state: open orders, verified payments, served log. In memory, saved to JSON."""
from __future__ import annotations

import json
import os
import string
import threading
import time
from dataclasses import asdict

from .verify import NOT_PAID, PAID, UNDERPAID, UNVERIFIED, Transfer, Verdict, verify

USDT_MASTER_FRIENDLY = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"


class Shift:
    def __init__(self, address: str, path: str | None = None, min_no_order: float = 0.0):
        self.address = address
        self.path = path
        self.min_no_order = min_no_order
        self.lock = threading.RLock()
        self.orders: dict[str, dict] = {}     # code -> {code, amount, created, status, tx}
        self.payments: dict[str, dict] = {}   # tx_hash -> payment row
        self.seq = 0
        self.started = int(time.time())
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("address") == address:
                    self.orders, self.payments, self.seq = d["orders"], d["payments"], d["seq"]
                    self.started = d.get("started", self.started)
            except (OSError, ValueError, KeyError):
                pass

    def save(self) -> None:
        if not self.path:
            return
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"address": self.address, "orders": self.orders, "payments": self.payments,
                           "seq": self.seq, "started": self.started}, f)
            for attempt in range(5):  # Windows: a reader may hold the file for a moment
                try:
                    os.replace(tmp, self.path)
                    return
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))

    # orders -------------------------------------------------------------
    def _next_code(self) -> str:
        self.seq += 1
        letter = string.ascii_uppercase[(self.seq // 100) % 26]
        return f"{letter}{self.seq % 100:02d}"

    def open_orders(self) -> dict[str, float]:
        return {c: o["amount"] for c, o in self.orders.items() if o["status"] in ("waiting", "underpaid")}

    def create_order(self, amount: float) -> dict:
        with self.lock:
            code = self._next_code()
            raw = int(round(amount * 1_000_000))
            o = {
                "code": code, "amount": round(amount, 2), "created": int(time.time()),
                "status": "waiting", "tx": None,
                # Tonkeeper-style deeplink; Gram Wallet / Tonkeeper / Telegram open it.
                "pay_link": f"ton://transfer/{self.address}?jetton={USDT_MASTER_FRIENDLY}&amount={raw}&text={code}",
            }
            self.orders[code] = o
            self.save()
            return o

    def cancel_order(self, code: str) -> bool:
        with self.lock:
            o = self.orders.get(code)
            if not o or o["status"] not in ("waiting", "underpaid"):
                return False
            o["status"] = "cancelled"
            self.save()
            return True

    # payments -----------------------------------------------------------
    def ingest(self, transfers: list[Transfer]) -> list[dict]:
        """Verify new transfers; return the rows that were added."""
        added = []
        with self.lock:
            for t in sorted(transfers, key=lambda x: x.time):
                prev = self.payments.get(t.tx_hash)
                if prev is not None and prev["status"] != UNVERIFIED:
                    continue  # already decided; an UNVERIFIED row is checked again
                v: Verdict = verify(t, self.open_orders(), min_no_order=self.min_no_order)
                if prev is not None and v.status == UNVERIFIED:
                    continue  # still cannot verify; keep the existing row
                row = {
                    "tx": t.tx_hash, "time": t.time, "status": v.status, "usdt": v.usdt,
                    "reason": v.reason, "order": v.order_code, "claimed": v.claimed,
                    "sender": t.sender, "comment": t.comment[:80], "symbol": t.symbol,
                    "checks": v.checks, "served": False, "hold": False,
                }
                if prev is not None:
                    row["served"], row["hold"] = prev.get("served", False), prev.get("hold", False)
                if v.order_code:
                    o = self.orders[v.order_code]
                    if v.status == PAID:
                        o["status"], o["tx"] = "paid", t.tx_hash
                    elif v.status == UNDERPAID:
                        o["status"] = "underpaid"
                self.payments[t.tx_hash] = row
                added.append(row)
            if added:
                self.save()
        return added

    def mark(self, tx: str, field: str) -> bool:
        with self.lock:
            p = self.payments.get(tx)
            if not p:
                return False
            p[field] = True
            if field == "served" and p.get("order") in self.orders:
                self.orders[p["order"]]["status"] = "served"
            self.save()
            return True

    def feed(self, limit: int = 30) -> list[dict]:
        rows = sorted(self.payments.values(), key=lambda r: r["time"], reverse=True)
        return [r for r in rows if r["status"] != "OTHER"][:limit]

    def summary(self) -> dict:
        rows = [r for r in self.payments.values() if r["time"] >= self.started]
        paid = [r for r in rows if r["status"] == PAID]
        return {
            "paid_count": len(paid),
            "paid_usdt": round(sum(r["usdt"] for r in paid), 2),
            "blocked": sum(1 for r in rows if r["status"] in NOT_PAID),
            "unverified": sum(1 for r in rows if r["status"] == UNVERIFIED),
            "waiting": sum(1 for o in self.orders.values() if o["status"] == "waiting"),
            "since": self.started,
        }
