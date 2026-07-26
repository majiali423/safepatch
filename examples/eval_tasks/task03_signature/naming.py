def format_name(first: str, last: str) -> str:
    """Format as 'First Last'."""
    return f"{first} {last}"


def initials(first: str, last: str) -> str:
    return f"{first[:1]}.{last[:1]}.".upper()
