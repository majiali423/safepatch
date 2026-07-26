"""Unrelated pricing helpers. Not the source of the failing tests."""


def apply_discount(price: float, percent: float) -> float:
    return price * (1 - percent / 100)


def tax(price: float, rate: float = 0.1) -> float:
    return price * rate
