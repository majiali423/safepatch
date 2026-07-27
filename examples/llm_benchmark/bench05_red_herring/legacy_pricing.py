"""Legacy pricing helpers — not used by the public API under test.

Do not modify this file to fix the bug; the active implementation is pricing.py.
"""


def old_final_price(amount: float, rate: float) -> float:
    return amount * (1.0 - rate)


def clearance_price(amount: float) -> float:
    return amount * 0.5
