"""PaidGlance backend: watches one Gram Wallet address on TON and verifies
every incoming payment. The G2 app and the counter page talk to this server.

Run:  python server/app.py            (port 8787)
Env:  PAIDGLANCE_ADDRESS   merchant wallet, user-friendly form (required for real use)
      PAIDGLANCE_POLL      seconds between chain polls (default 5)
      PAIDGLANCE_MIN_NO_ORDER  USD₮ below this with no order code is ignored as dust (default 0.10)
      TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   optional: also post verdicts to the shop's Telegram
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from paidglance import ton  # noqa: E402
from paidglance.store import Shift  # noqa: E402
from paidglance.verify import NOT_PAID, PAID, UNVERIFIED, USDT_MASTER  # noqa: E402
from paidglance.voice import parse_command  # noqa: E402

# Default: a real, busy TON merchant wallet (a Telegram Stars reseller that takes
# USD₮ with an order reference in every comment). Read-only; used so the demo
# runs on live chain data without anyone's account. Replace with your own.
DEMO_ADDRESS = "EQCFJEP4WZ_mpdo0_kMEmsTgvrMHG7K_tWY16pQhKHwoOoy2"
ADDRESS = os.environ.get("PAIDGLANCE_ADDRESS", DEMO_ADDRESS)
POLL = float(os.environ.get("PAIDGLANCE_POLL", "5"))
# Real USD₮ that carries no open order code and is below this amount is dust
# (address-poisoning style spam), not a sale: hidden instead of shown as PAID.
MIN_NO_ORDER = float(os.environ.get("PAIDGLANCE_MIN_NO_ORDER", "0.10"))
PORT = int(os.environ.get("PORT", "8787"))
DATA = Path(os.environ.get("PAIDGLANCE_DATA", Path(__file__).parent / "shift.json"))
DEMO_AUDIO = Path(__file__).parent / "demo_audio"

shift = Shift(ADDRESS, str(DATA), min_no_order=MIN_NO_ORDER)
status = {"last_poll": 0, "last_error": "", "polls": 0}


def notify_telegram(row: dict) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    if row["status"] == PAID:
        text = f"PAID {row['order'] or ''} {row['usdt']:.2f} USD₮ ({row['reason']})"
    elif row["status"] in NOT_PAID:
        text = f"NOT PAID {row['order'] or ''}: {row['reason']} (notification said: {row['claimed']})"
    elif row["status"] == UNVERIFIED:
        text = f"CAN'T VERIFY {row['order'] or ''}: {row['reason']}. Do not hand over yet."
    else:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
    except Exception as e:  # never let a chat outage stop verification
        print("telegram:", e, file=sys.stderr)


def poll_once() -> list[dict]:
    seen = shift.payments
    transfers = ton.jetton_transfers_in(ADDRESS, limit=25)
    transfers += ton.ton_transfers_in(ADDRESS, limit=25)
    for t in transfers:
        prev = seen.get(t.tx_hash)
        fresh = prev is None or prev["status"] == UNVERIFIED  # retry failed checks
        if fresh and t.kind == "jetton" and t.jetton_master == USDT_MASTER:
            t.canonical_wallet = ton.canonical_usdt_wallet(t.sender)
    added = shift.ingest(transfers)
    for row in added:
        notify_telegram(row)
    return added


def poller() -> None:
    while True:
        try:
            poll_once()
            status["last_error"] = ""
        except Exception as e:
            status["last_error"] = f"{type(e).__name__}: {e}"[:200]
            print("poll:", status["last_error"], file=sys.stderr)
        status["last_poll"] = int(time.time())
        status["polls"] += 1
        time.sleep(POLL)


def state() -> dict:
    with shift.lock:
        orders = sorted(shift.orders.values(), key=lambda o: o["created"], reverse=True)[:10]
        return {
            "address": ADDRESS, "now": int(time.time()), "summary": shift.summary(),
            "feed": shift.feed(20), "orders": orders, "poll": status,
        }


def run_command(text: str) -> dict:
    cmd, amount = parse_command(text)
    res: dict = {"text": text, "command": cmd}
    if cmd == "charge":
        res["order"] = shift.create_order(amount)
    elif cmd == "too_large":
        res["error"] = f"{amount:.2f} is over the 1,000 USD₮ counter limit"
    elif cmd == "cancel":
        waiting = [o for o in shift.orders.values() if o["status"] == "waiting"]
        if waiting:
            last = max(waiting, key=lambda o: o["created"])
            shift.cancel_order(last["code"])
            res["cancelled"] = last["code"]
    elif cmd == "total":
        res["summary"] = shift.summary()
    return res


_replayed: set[str] = set()


def replay_fake() -> dict:
    """Demo only (PAIDGLANCE_DEMO=1): push one recorded mainnet transfer of a
    fake "USDT" jetton (from eval/dataset.json) through the same verifier, as
    if it had just arrived. The row is marked replay=True and says so."""
    from dataclasses import fields
    from paidglance.verify import Transfer
    ds = json.loads((Path(__file__).parents[1] / "eval" / "dataset.json").read_text(encoding="utf-8"))
    keys = {f.name for f in fields(Transfer)}
    for r in sorted(ds["fake"], key=lambda r: -r["amount_raw"] / 10 ** (r["decimals"] or 0)):
        if r["tx_hash"] in _replayed or r["aborted"] or not r["symbol"].strip().upper().startswith("USD"):
            continue
        _replayed.add(r["tx_hash"])
        t = Transfer(**{k: v for k, v in r.items() if k in keys})
        original_time = t.time
        t.time = int(time.time())
        t.tx_hash = "replay:" + t.tx_hash
        rows = shift.ingest([t])
        for row in rows:
            row["replay"] = True
            row["reason"] += f" (replayed mainnet tx from {time.strftime('%Y-%m-%d', time.gmtime(original_time))})"
        shift.save()
        return {"ok": True, "rows": rows}
    return {"ok": False}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass

    def do_OPTIONS(self):
        self._send(204, b"")

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ValueError("body too large")
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/api/state":
            return self._send(200, state())
        if p.startswith("/demo/") and p.endswith(".pcm"):
            f = DEMO_AUDIO / Path(p).name
            if f.exists():
                return self._send(200, f.read_bytes(), "application/octet-stream")
        if p == "/api/health":
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        try:
            if p == "/api/orders":
                amount = float(json.loads(self._body() or b"{}").get("amount", 0))
                if not 0 < amount <= 1000:
                    return self._send(400, {"error": "amount must be between 0 and 1000"})
                return self._send(200, shift.create_order(amount))
            if p.startswith("/api/orders/") and p.endswith("/cancel"):
                return self._send(200, {"ok": shift.cancel_order(p.split("/")[3])})
            if p.startswith("/api/payments/"):
                _, _, _, tx, action = p.split("/", 4)
                if action not in ("served", "hold"):
                    return self._send(400, {"error": "unknown action"})
                return self._send(200, {"ok": shift.mark(urllib.parse.unquote(tx), action)})
            if p == "/api/voice":
                from paidglance.stt import transcribe_pcm16
                text = transcribe_pcm16(self._body())
                return self._send(200, run_command(text))
            if p == "/api/replay-fake" and os.environ.get("PAIDGLANCE_DEMO") == "1":
                return self._send(200, replay_fake())
            if p == "/api/voice-text":
                return self._send(200, run_command(json.loads(self._body() or b"{}").get("text", "")))
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"[:200]})
        self._send(404, {"error": "not found"})


def main():
    if os.environ.get("PAIDGLANCE_NO_STT") != "1":
        from paidglance.stt import warm_up
        warm_up()
    threading.Thread(target=poller, daemon=True).start()
    print(f"PaidGlance watching {ADDRESS} on :{PORT}")
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
