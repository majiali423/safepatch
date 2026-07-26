def available(stock: int, reserved: int) -> int:
    """Return units available for sale."""
    return stock - reserved


def can_fulfill(stock: int, reserved: int, qty: int) -> bool:
    return available(stock, reserved) >= qty
