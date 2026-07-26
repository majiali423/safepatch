from naming import format_name


def display_user(first: str, last: str) -> str:
    # Caller expects format_name(first, last) -> "First Last"
    return format_name(last, first)
