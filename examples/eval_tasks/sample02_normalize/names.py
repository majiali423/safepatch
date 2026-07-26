def normalize_name(s: str) -> str:
    """Strip outer whitespace, collapse inner spaces, lowercase.

    Examples:
      " Alice " -> "alice"
      " BOB " -> "bob"
      "" -> ""
      "  Mary Jane  " -> "mary jane"
    """
    return s
