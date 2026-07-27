def normalize_date(s: str) -> str:
    """Normalize to YYYY-MM-DD.

    Accept YYYY-MM-DD or YYYY/M/D (month/day may be 1-2 digits).
    Raise ValueError for invalid formats or impossible calendar dates.
    """
    # BUG: only accepts '/', does not zero-pad
    parts = s.split('/')
    if len(parts) != 3:
        raise ValueError('bad format')
    y, m, d = parts
    return f"{y}-{m}-{d}"
