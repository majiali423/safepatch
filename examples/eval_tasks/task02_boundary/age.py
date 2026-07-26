def can_vote(age: int) -> bool:
    """Return True if age is 18 or older."""
    return age > 18


def describe(age: int) -> str:
    if can_vote(age):
        return "adult"
    return "minor"
