def slugify(title: str) -> str:
    """Convert a title to a URL slug.

    Rules:
    - lowercase
    - whitespace runs become a single '-'
    - keep only [a-z0-9-]; drop other characters
    - collapse consecutive '-'
    - strip leading/trailing '-'
    - empty / all-stripped input -> ""
    """
    # BUG: only lowercases; ignores punctuation/spaces rules
    return title.lower()
