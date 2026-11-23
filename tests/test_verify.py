import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from paidglance.verify import (USDT_MASTER, Transfer, verify, naive_says_paid, looks_like_usdt,
                               PAID, UNDERPAID, FAKE_TOKEN, COMMENT_ONLY, FAILED, OTHER, UNVERIFIED)
from paidglance import ton
from paidglance.voice import parse_command, parse_amount
from paidglance.store import Shift

FAKE = "0:41ea6270d36febb14d0ef4000000000000000000000000000000000000000000"


def usdt(amount, comment="", **kw):
    base = dict(tx_hash="h", time=1, kind="jetton", amount_raw=int(amount * 1e6), comment=comment,
                sender="0:aa", jetton_master=USDT_MASTER, symbol="USD₮", decimals=6, source_wallet="0:bb",
                canonical_wallet="0:bb")  # Tether's master confirmed the sender's wallet
    base.update(kw)
    return Transfer(**base)


class VerifyTest(unittest.TestCase):
    def test_real_usdt_pays_order(self):
        v = verify(usdt(4.5, "A17"), {"A17": 4.5})
        self.assertEqual((v.status, v.order_code), (PAID, "A17"))

    def test_underpaid(self):
        self.assertEqual(verify(usdt(4.0, "a17"), {"A17": 4.5}).status, UNDERPAID)

    def test_tolerance_half_cent(self):
        self.assertEqual(verify(usdt(4.496, "A17"), {"A17": 4.5}).status, PAID)

    def test_fake_master_with_usdt_symbol(self):
        for sym in ["USDT", "$USD₮", "USDT⠀⠀", " USDT", "ＵＳＤＴ"]:
            v = verify(usdt(5000, "A17", jetton_master=FAKE, symbol=sym), {"A17": 4.5})
            self.assertEqual(v.status, FAKE_TOKEN, sym)
            self.assertEqual(v.usdt, 0.0)

    def test_forged_wallet_claiming_official_master(self):
        t = usdt(10, "A17", canonical_wallet="0:cc")  # Tether says sender's wallet is 0:cc, record says 0:bb
        self.assertEqual(verify(t, {"A17": 10}).status, FAKE_TOKEN)

    def test_canonical_wallet_ok(self):
        t = usdt(10, "A17", canonical_wallet="0:BB")
        self.assertEqual(verify(t, {"A17": 10}).status, PAID)

    def test_forged_source_wallet(self):
        # A record that names Tether's master but comes from a jetton wallet the
        # master did not deploy for this sender: forged, whatever it claims.
        for forged in ["0:deadbeef", "", "0:bb0"]:
            t = usdt(5000, "A17", source_wallet=forged, canonical_wallet="0:bb")
            v = verify(t, {"A17": 4.5})
            self.assertEqual(v.status, FAKE_TOKEN, forged)
            self.assertEqual(v.usdt, 0.0)
            self.assertFalse(v.checks["canonical_wallet"])

    def test_lookup_failed_is_unverified_not_paid(self):
        # tonapi down / rate limited: canonical_wallet stays None.
        t = usdt(4.5, "A17", canonical_wallet=None)
        v = verify(t, {"A17": 4.5})
        self.assertEqual(v.status, UNVERIFIED)
        self.assertEqual((v.usdt, v.order_code), (0.0, "A17"))
        self.assertEqual(verify(usdt(4.5, canonical_wallet=None)).status, UNVERIFIED)  # no order either

    def test_forged_wallet_during_outage_never_paid(self):
        t = usdt(5000, "A17", source_wallet="0:deadbeef", canonical_wallet=None)
        self.assertNotEqual(verify(t, {"A17": 4.5}).status, PAID)

    def test_eval_mode_scores_master_only_when_not_looked_up(self):
        self.assertEqual(verify(usdt(4.5, canonical_wallet=None), require_canonical=False).status, PAID)

    def test_ton_lookup_failure_returns_none(self):
        orig = ton._get
        def boom(*a, **k): raise OSError("tonapi down")
        ton._get = boom
        try:
            ton._canon_cache.clear()
            self.assertIsNone(ton.canonical_usdt_wallet("0:aa"))
            self.assertNotIn("0:aa", ton._canon_cache)  # failures are not cached
        finally:
            ton._get = orig

    def test_min_amount_without_order_code(self):
        self.assertEqual(verify(usdt(0.01), min_no_order=0.10).status, OTHER)       # dust, hidden
        self.assertEqual(verify(usdt(0.50), min_no_order=0.10).status, PAID)
        self.assertEqual(verify(usdt(0.05, "A17"), {"A17": 0.05}, min_no_order=0.10).status, PAID)  # order wins
        self.assertEqual(verify(usdt(0.01)).status, PAID)                            # off by default

    def test_comment_only_scam(self):
        t = Transfer(tx_hash="h", time=1, kind="ton", amount_raw=1_000_000, comment="Received +5,000 USDT")
        self.assertEqual(verify(t).status, COMMENT_ONLY)
        self.assertTrue(naive_says_paid(t))

    def test_aborted(self):
        self.assertEqual(verify(usdt(4.5, "A17", aborted=True), {"A17": 4.5}).status, FAILED)

    def test_other_jetton(self):
        self.assertEqual(verify(usdt(1, jetton_master=FAKE, symbol="NOT")).status, OTHER)

    def test_code_must_be_whole_token(self):
        self.assertIsNone(verify(usdt(4.5, "A170"), {"A17": 4.5}).order_code)
        self.assertEqual(verify(usdt(4.5, "Ref#A17"), {"A17": 4.5}).order_code, "A17")

    def test_lookalike(self):
        self.assertTrue(looks_like_usdt("USD₮"))
        self.assertFalse(looks_like_usdt("USDC"))


class VoiceTest(unittest.TestCase):
    def test_charge(self):
        cases = {"Charge 4.50.": 4.5, "charge four fifty": 4.5, "Charge $12.": 12.0,
                 "new order twelve twenty five": 12.25, "charge 4 dollars 50": 4.5, "Charge 30": 30.0}
        for text, amt in cases.items():
            self.assertEqual(parse_command(text), ("charge", amt), text)

    def test_other_commands(self):
        self.assertEqual(parse_command("Total.")[0], "total")
        self.assertEqual(parse_command("cancel that")[0], "cancel")
        self.assertEqual(parse_command("served")[0], "served")
        self.assertEqual(parse_command("what's the weather")[0], "unknown")
        self.assertEqual(parse_command("charge 5000")[0], "too_large")


class ShiftTest(unittest.TestCase):
    def test_order_lifecycle(self):
        s = Shift("EQtest")
        o = s.create_order(4.5)
        self.assertIn("text=" + o["code"], o["pay_link"])
        rows = s.ingest([usdt(4.5, o["code"], tx_hash="t1")])
        self.assertEqual(rows[0]["status"], PAID)
        self.assertEqual(s.orders[o["code"]]["status"], "paid")
        s.mark("t1", "served")
        self.assertEqual(s.orders[o["code"]]["status"], "served")
        self.assertEqual(s.ingest([usdt(4.5, o["code"], tx_hash="t1")]), [])  # idempotent

    def test_unverified_is_rechecked(self):
        s = Shift("EQtest")
        o = s.create_order(4.5)
        rows = s.ingest([usdt(4.5, o["code"], tx_hash="t2", canonical_wallet=None)])
        self.assertEqual(rows[0]["status"], UNVERIFIED)
        self.assertEqual(s.orders[o["code"]]["status"], "waiting")   # not paid while unverified
        self.assertEqual(s.summary()["paid_count"], 0)
        self.assertEqual(s.ingest([usdt(4.5, o["code"], tx_hash="t2", canonical_wallet=None)]), [])  # still down
        rows = s.ingest([usdt(4.5, o["code"], tx_hash="t2")])        # lookup works again
        self.assertEqual(rows[0]["status"], PAID)
        self.assertEqual(s.orders[o["code"]]["status"], "paid")
        self.assertEqual(s.summary()["unverified"], 0)

    def test_shift_dust_minimum(self):
        s = Shift("EQtest", min_no_order=0.10)
        rows = s.ingest([usdt(0.01, tx_hash="d1")])
        self.assertEqual(rows[0]["status"], OTHER)
        self.assertEqual(s.feed(), [])


if __name__ == "__main__":
    unittest.main()
