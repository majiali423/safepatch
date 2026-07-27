def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or touching inclusive ranges."""
    # BUG: assumes input already sorted; touching (gap=1) not merged
    if not ranges:
        return []
    out = [ranges[0]]
    for a, b in ranges[1:]:
        la, lb = out[-1]
        if a <= lb:
            out[-1] = (la, max(lb, b))
        else:
            out.append((a, b))
    return out
