import pytest

from scores import grade, set_score


def test_valid_score():
    assert set_score(85) == 85
    assert grade(95) == "A"


def test_negative_rejected():
    with pytest.raises(ValueError):
        set_score(-1)


def test_too_large_rejected():
    with pytest.raises(ValueError):
        set_score(101)
