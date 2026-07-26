from age import can_vote, describe


def test_underage():
    assert can_vote(17) is False
    assert describe(17) == "minor"


def test_exactly_eighteen():
    assert can_vote(18) is True
    assert describe(18) == "adult"


def test_over_eighteen():
    assert can_vote(21) is True
