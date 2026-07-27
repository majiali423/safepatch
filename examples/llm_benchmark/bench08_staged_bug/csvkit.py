def clean_row(row: dict) -> dict:
    """Return a cleaned shallow copy of row.

    - strip whitespace from keys and string values
    - convert empty-string values to None
    - leave non-strings unchanged
    """
    # Multi-defect buggy version (observe repair loops; rounds not guaranteed):
    # 1) does not strip keys
    # 2) does not convert "" to None
    out = {}
    for k, v in row.items():
        if isinstance(v, str):
            out[k] = v.strip()
        else:
            out[k] = v
    return out
