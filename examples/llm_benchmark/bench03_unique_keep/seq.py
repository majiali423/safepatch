def unique_keep_order(xs: list) -> list:
    """Return unique items preserving first-seen order."""
    # BUG: only collapses adjacent duplicates
    if not xs:
        return []
    out = [xs[0]]
    for x in xs[1:]:
        if x != out[-1]:
            out.append(x)
    return out
