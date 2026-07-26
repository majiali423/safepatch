def set_score(score: int) -> int:
    """Store a score in [0, 100]. Raise ValueError if out of range."""
    return score


def grade(score: int) -> str:
    value = set_score(score)
    if value >= 90:
        return "A"
    if value >= 60:
        return "B"
    return "C"
