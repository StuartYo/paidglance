"""Short voice commands for the counter. A fixed grammar, no model.

  "charge 4.50" / "charge four fifty" / "new order 12 dollars"  -> ("charge", 4.5)
  "total" / "how much today"                                   -> ("total", None)
  "served" / "done"                                            -> ("served", None)
  "cancel" / "never mind"                                      -> ("cancel", None)
Anything else -> ("unknown", None): the glasses say so instead of guessing.
"""
from __future__ import annotations

import re

_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}

MAX_CHARGE = 1000.0  # a glance-confirmed counter order above this is refused


def _words_to_numbers(words: list[str]) -> list[int]:
    """'four fifty' -> [4, 50]; 'twenty five' -> [25]; 'hundred' handled."""
    nums: list[int] = []
    cur: int | None = None
    for w in words:
        if w in _UNITS:
            if cur is not None and cur >= 20 and cur % 10 == 0 and _UNITS[w] < 10:
                cur += _UNITS[w]
            else:
                if cur is not None:
                    nums.append(cur)
                cur = _UNITS[w]
        elif w in _TENS:
            if cur is not None:
                nums.append(cur)
            cur = _TENS[w]
        elif w == "hundred" and cur is not None:
            cur *= 100
        elif cur is not None:
            nums.append(cur)
            cur = None
    if cur is not None:
        nums.append(cur)
    return nums


def parse_amount(text: str) -> float | None:
    t = text.lower().replace(",", "")
    m = re.search(r"\$?\s*(\d+(?:\.\d{1,2})?)", t)
    if m:
        whole = float(m.group(1))
        # "4 50" or "4 dollars 50"
        rest = t[m.end():]
        m2 = re.match(r"\s*(?:dollars?|usd|usdt|bucks)?\s*(?:and\s*)?(\d{1,2})\b(?!\.)", rest)
        if m2 and "." not in m.group(1):
            whole += int(m2.group(1)) / 100
        return round(whole, 2)
    words = re.findall(r"[a-z]+", t)
    nums = _words_to_numbers(words)
    if not nums:
        return None
    if len(nums) == 1:
        return float(nums[0])
    # "four fifty" -> 4.50, "twelve twenty five" -> 12.25
    return round(nums[0] + nums[1] / 100, 2) if nums[1] < 100 else float(nums[0])


def parse_command(text: str) -> tuple[str, float | None]:
    t = (text or "").lower().strip()
    if not t:
        return ("unknown", None)
    if re.search(r"\b(cancel|never ?mind|scratch that)\b", t):
        return ("cancel", None)
    if re.search(r"\b(charge|new order|bill|order for|that's|thats|ring up)\b", t):
        amt = parse_amount(re.split(r"charge|new order|bill|order for|that's|thats|ring up", t, maxsplit=1)[-1])
        if amt is None or amt <= 0:
            return ("unknown", None)
        if amt > MAX_CHARGE:
            return ("too_large", amt)
        return ("charge", amt)
    if re.search(r"\b(total|how much|today|sales)\b", t):
        return ("total", None)
    if re.search(r"\b(served|done|handed|next)\b", t):
        return ("served", None)
    return ("unknown", None)
