"""Evaluation on real TON mainnet transfers.

Question: if a shop decides "paid / not paid" from what a wallet notification
says (token symbol + amount + comment), how often is it wrong, and how often
is PaidGlance's chain check wrong?

  real set : latest transfers of Tether's official USD₮ jetton
  fake set : every transfer we can find of jettons whose symbol renders as
             USDT/USD₮ but whose master is not Tether's (listed by tonapi)
Ground truth: Tether's master contract (and, for the real set, the canonical
jetton-wallet check). Writes eval/dataset.json (raw records) and eval/results.json.

python eval/run_eval.py            # fetch live data (about 2 minutes, free API tier)
python eval/run_eval.py --offline  # recompute from eval/dataset.json
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from paidglance import ton  # noqa: E402
from paidglance.verify import (NOT_PAID, PAID, USDT_MASTER, Transfer, looks_like_usdt,  # noqa: E402
                               naive_says_paid, verify)

OUT = Path(__file__).parent


def lookalike_masters(pages: int = 3) -> list[dict]:
    found = []
    for i in range(pages):
        d = ton._get("tonapi", f"{ton.TONAPI}/jettons?limit=1000&offset={i * 1000}")
        for j in d.get("jettons", []):
            m = j.get("metadata", {})
            addr = (m.get("address") or "").lower()
            if looks_like_usdt(m.get("symbol", "")) and addr != USDT_MASTER:
                found.append({"master": addr, "symbol": m.get("symbol"), "name": m.get("name"),
                              "verification": j.get("verification"), "holders": j.get("holders_count")})
    return found


def fetch() -> dict:
    fakes = lookalike_masters()
    fake_transfers = []
    budget = 150  # tonapi calls for transfer histories (free tier ~1/s)
    for f in fakes:
        # toncenter does not index most spam jettons, so use tonapi: who holds
        # the fake token, then the actual transfer events into each holder.
        try:
            holders = ton._get("tonapi", f"{ton.TONAPI}/jettons/{f['master']}/holders?limit=20").get("addresses", [])
        except Exception as e:
            f["fetch_error"] = str(e)[:80]
            continue
        f["holders_seen"] = len(holders)
        for h in holders:
            owner = (h.get("owner") or {}).get("address", "")
            if not owner or budget <= 0:
                continue
            budget -= 1
            try:
                ev = ton._get("tonapi", f"{ton.TONAPI}/accounts/{owner}/jettons/{f['master']}/history?limit=10")
            except Exception:
                continue
            for e in ev.get("events", []):
                for a in e.get("actions", []):
                    jt = a.get("JettonTransfer")
                    if a.get("type") != "JettonTransfer" or not jt:
                        continue
                    if (jt.get("recipient") or {}).get("address", "").lower() != owner.lower():
                        continue
                    fake_transfers.append(asdict(Transfer(
                        tx_hash=e.get("event_id", ""), time=int(e.get("timestamp") or 0), kind="jetton",
                        amount_raw=int(jt.get("amount") or 0), comment=(jt.get("comment") or "")[:200],
                        sender=(jt.get("sender") or {}).get("address", ""), jetton_master=f["master"],
                        symbol=(jt.get("jetton") or {}).get("symbol") or f["symbol"],
                        decimals=int((jt.get("jetton") or {}).get("decimals") or 6),
                        aborted=a.get("status") != "ok")))
    real = ton.jetton_transfers_of_master(USDT_MASTER, limit=60)
    for t in real[:40]:  # canonical-wallet check on 40 (tonapi free tier is ~1 req/s)
        t.canonical_wallet = ton.canonical_usdt_wallet(t.sender)
    return {"fetched_at": int(time.time()), "lookalike_masters": fakes,
            "fake": fake_transfers, "real": [asdict(t) for t in real]}


def score(ds: dict) -> dict:
    def run(rows, truth_paid):
        n = len(rows)
        naive_wrong = ours_wrong = 0
        for r in rows:
            t = Transfer(**r)
            if naive_says_paid(t) != truth_paid:
                naive_wrong += 1
            # Rows we did not look up on tonapi are scored on the master check
            # alone (require_canonical=False); the live server never does that.
            ours_paid = verify(t, require_canonical=False).status == PAID
            if ours_paid != truth_paid:
                ours_wrong += 1
        return {"n": n, "naive_wrong": naive_wrong, "ours_wrong": ours_wrong}

    fake = run(ds["fake"], truth_paid=False)
    real = run([r for r in ds["real"] if not r["aborted"]], truth_paid=True)
    checked = [r for r in ds["real"] if r.get("canonical_wallet")]
    canon_ok = sum(1 for r in checked if r["canonical_wallet"].lower() == r["source_wallet"].lower())
    fake_usd_claimed = sum(r["amount_raw"] / 10 ** (r["decimals"] or 0) for r in ds["fake"])
    return {
        "fetched_at": ds["fetched_at"],
        "fetched_at_utc": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ds["fetched_at"])),
        "lookalike_usdt_jettons_found": len(ds["lookalike_masters"]),
        "lookalike_blacklisted_by_tonapi": sum(1 for m in ds["lookalike_masters"] if m["verification"] == "blacklist"),
        "fake_transfers": fake,
        "fake_usdt_face_value": round(fake_usd_claimed, 2),
        "real_transfers": real,
        "real_master_checked": real["n"],
        "canonical_wallet_checked": len(checked),
        "canonical_wallet_match": canon_ok,
        "note": ("All real rows pass the Tether master check; only the first canonical_wallet_checked "
                 "rows also had the canonical jetton-wallet lookup (tonapi free tier). The live "
                 "server looks up every USD₮ transfer and says UNVERIFIED if the lookup fails."),
        "baseline": "notification text: symbol looks like USDT and amount > 0 (or comment claims USD received)",
        "baseline_name": "naive notification baseline",
        "ours": "verify(): Tether master address + canonical jetton wallet (where looked up) + transaction not aborted",
    }


def main():
    ds_path = OUT / "dataset.json"
    if "--offline" in sys.argv:
        ds = json.loads(ds_path.read_text(encoding="utf-8"))
    else:
        ds = fetch()
        ds_path.write_text(json.dumps(ds, ensure_ascii=False, indent=1), encoding="utf-8")
    res = score(ds)
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
