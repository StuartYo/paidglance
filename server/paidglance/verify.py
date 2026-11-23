"""Payment verification. Pure functions, no network.

The glasses only ever print what `verify()` returns. No language model writes
the PAID line: the verdict comes from chain facts (which contract, how much,
did the transaction succeed, does the comment carry the order code).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Tether USD on TON (official jetton master, raw form). Source: tether.to and
# tonviewer.com/EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs
USDT_MASTER = "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"
USDT_DECIMALS = 6

# Tolerance for "paid enough": half a cent.
AMOUNT_TOLERANCE = 0.005

PAID = "PAID"              # real USD₮ arrived, amount covers the order (or no order to compare)
UNDERPAID = "UNDERPAID"    # real USD₮, but less than the order amount
FAKE_TOKEN = "FAKE_TOKEN"  # token calls itself USDT but is not Tether's contract
COMMENT_ONLY = "COMMENT_ONLY"  # a tiny TON transfer whose comment claims a payment
FAILED = "FAILED"          # transaction aborted on chain: nothing arrived
OTHER = "OTHER"            # some other asset, or dust below the no-order minimum; hidden
UNVERIFIED = "UNVERIFIED"  # claims Tether's contract, but the sender-wallet check could not run

NOT_PAID = {FAKE_TOKEN, COMMENT_ONLY, FAILED}


@dataclass
class Transfer:
    """One incoming transfer, normalized from the indexer."""
    tx_hash: str
    time: int
    kind: str                 # "jetton" or "ton"
    amount_raw: int           # smallest units (jetton units, or nanoton)
    comment: str = ""
    sender: str = ""          # owner address of the payer
    jetton_master: str = ""   # raw address, jetton transfers only
    symbol: str = ""          # what the token's metadata calls itself
    decimals: int = 6
    source_wallet: str = ""   # payer's jetton wallet (jetton transfers only)
    canonical_wallet: str | None = None  # Tether master's answer for get_wallet_address(sender);
                                         # None = not looked up, or the lookup failed
    aborted: bool = False


@dataclass
class Verdict:
    status: str
    usdt: float               # real USD₮ that arrived (0 when not paid)
    reason: str
    order_code: str | None = None
    claimed: str = ""         # what a notification would have shown
    checks: dict = field(default_factory=dict)


def _norm_symbol(sym: str) -> str:
    """Fold look-alike symbols: '$USD₮', 'USDT⠀⠀', ' USDT', fullwidth letters."""
    s = unicodedata.normalize("NFKC", sym or "")
    s = "".join(ch for ch in s if ch.isalnum() or ch == "₮")
    return s.upper().replace("₮", "T")


def looks_like_usdt(symbol: str) -> bool:
    return _norm_symbol(symbol) == "USDT"


_CLAIM = re.compile(r"(receiv|deposit|paid|payment|usd|\+\s*\d)", re.I)


def find_order_code(comment: str, open_codes: list[str]) -> str | None:
    """Match an order code as a whole token in the comment (case-insensitive)."""
    if not comment:
        return None
    tokens = {t.upper() for t in re.findall(r"[A-Za-z0-9]+", comment)}
    for code in open_codes:
        if code.upper() in tokens:
            return code
    return None


def claimed_text(t: Transfer) -> str:
    """What a wallet push notification shows: symbol + amount + comment."""
    if t.kind == "jetton":
        amt = t.amount_raw / 10 ** (t.decimals or 0)
        return f"+{amt:,.2f} {t.symbol}".strip()
    if t.comment:
        return t.comment[:60]
    return f"+{t.amount_raw / 1e9:.4f} GRAM"


def verify(t: Transfer, orders: dict[str, float] | None = None, *,
           require_canonical: bool = True, min_no_order: float = 0.0) -> Verdict:
    """Decide what the glasses may say about one incoming transfer.

    orders: open orders, code -> expected USD₮ amount.
    require_canonical: a transfer that names Tether's master is only PAID once
        Tether's master has confirmed the sender's jetton wallet. If that lookup
        did not happen or failed (canonical_wallet is None), the verdict is
        UNVERIFIED, never PAID. The live server always uses True. The offline
        evaluation passes False for rows it did not look up, and reports them.
    min_no_order: real USD₮ without a matching order code below this amount is
        treated as dust (OTHER, hidden) instead of PAID. 0 = off.
    """
    orders = orders or {}
    code = find_order_code(t.comment, list(orders))
    claimed = claimed_text(t)
    checks = {
        "succeeded": not t.aborted,
        "official_master": t.kind == "jetton" and t.jetton_master.lower() == USDT_MASTER,
        "canonical_wallet": None,
        "order_code": code,
    }

    if t.aborted:
        return Verdict(FAILED, 0.0, "transaction failed on chain", code, claimed, checks)

    if t.kind == "ton":
        if _CLAIM.search(t.comment or ""):
            return Verdict(COMMENT_ONLY, 0.0,
                           f"only {t.amount_raw / 1e9:.4f} GRAM sent; the text is a comment",
                           code, claimed, checks)
        return Verdict(OTHER, 0.0, "GRAM transfer, not USD₮", code, claimed, checks)

    # jetton
    if t.jetton_master.lower() != USDT_MASTER:
        if looks_like_usdt(t.symbol):
            return Verdict(FAKE_TOKEN, 0.0, "calls itself USDT, not Tether's contract",
                           code, claimed, checks)
        return Verdict(OTHER, 0.0, f"{t.symbol or 'unknown token'}, not USD₮", code, claimed, checks)

    # Official master claimed. The sender's jetton wallet must be the one
    # Tether's master computes for that sender, or the record is forged.
    if t.canonical_wallet is not None:
        ok = bool(t.source_wallet) and t.canonical_wallet.lower() == t.source_wallet.lower()
        checks["canonical_wallet"] = ok
        if not ok:
            return Verdict(FAKE_TOKEN, 0.0, "sender wallet is not a Tether wallet",
                           code, claimed, checks)
    elif require_canonical:
        # Fail closed: no answer from Tether's master means we cannot tell a
        # real USD₮ wallet from a forged record. Not PAID; checked again next poll.
        return Verdict(UNVERIFIED, 0.0, "Tether wallet check failed, will retry",
                       code, claimed, checks)

    usdt = t.amount_raw / 10 ** USDT_DECIMALS
    if code is not None:
        due = orders[code]
        if usdt + AMOUNT_TOLERANCE < due:
            return Verdict(UNDERPAID, usdt, f"{usdt:.2f} of {due:.2f} USD₮", code, claimed, checks)
        return Verdict(PAID, usdt, f"order {code} covered", code, claimed, checks)
    if usdt < min_no_order:
        return Verdict(OTHER, 0.0, f"{usdt:.2f} USD₮ with no order code, under the {min_no_order:.2f} minimum",
                       None, claimed, checks)
    return Verdict(PAID, usdt, "real USD₮", None, claimed, checks)


def naive_says_paid(t: Transfer) -> bool:
    """Baseline: judge from the notification text alone, as a person glancing
    at a wallet push (or a model reading it) would."""
    if t.kind == "jetton":
        return looks_like_usdt(t.symbol) and t.amount_raw > 0
    return bool(re.search(r"(receiv|deposit|paid).*usd|usd.*(receiv|deposit|paid)|\+\s*[\d,]+\s*usd",
                          t.comment or "", re.I))
