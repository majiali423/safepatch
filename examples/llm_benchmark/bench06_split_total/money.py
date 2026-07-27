"""Money helpers (correct — not part of the required fix)."""


def as_cents(yuan: float) -> int:
    return int(round(yuan * 100))
