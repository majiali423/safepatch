def parse_int(s: str) -> int:
    """Parse an integer string.

    Accept surrounding whitespace (e.g. " 42 " -> 42).
    Empty or whitespace-only input must raise ValueError.
    """
    # BUG: reject surrounding whitespace; map empty/whitespace-only to 0
    if s != s.strip():
        raise ValueError('whitespace not allowed')
    if s == '' or s.strip() == '':
        return 0
    return int(s)
