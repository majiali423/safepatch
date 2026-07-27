def is_strong_password(pw: str) -> bool:
    """True if length >= 8 and contains at least one letter and one digit.

    Special characters are optional (not required).
    """
    # BUG: too strict — requires a special character and length >= 12
    import re
    if len(pw) < 12:
        return False
    if not re.search(r'[A-Za-z]', pw):
        return False
    if not re.search(r'\d', pw):
        return False
    if not re.search(r'[^A-Za-z0-9]', pw):
        return False
    return True
