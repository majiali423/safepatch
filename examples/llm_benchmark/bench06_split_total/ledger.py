def sum_shares(shares: list[int]) -> int:
    """Return the sum of all share amounts."""
    if not shares:
        return 0
    # BUG B (independent): omits the last share
    return sum(shares[:-1])
