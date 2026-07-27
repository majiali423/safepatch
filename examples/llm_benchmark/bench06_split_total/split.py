def split_equal(total: int, n: int) -> list[int]:
    """Split total into n non-negative integer shares.

    Shares must sum exactly to total. When total is not divisible by n,
    give the remainder as +1 to the first (total % n) shares.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if total < 0:
        raise ValueError("total must be non-negative")
    # BUG A: drops remainder so sum may be < total
    base = total // n
    return [base] * n
