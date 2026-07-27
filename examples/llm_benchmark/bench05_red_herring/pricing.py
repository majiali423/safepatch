def final_price(amount: float, rate: float) -> float:
    """Return price after discount rate in [0, 1].

    final_price = amount * (1 - rate)
    """
    # BUG: multiplies by rate instead of (1 - rate)
    return amount * rate
