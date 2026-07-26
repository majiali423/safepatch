import pytest

from calculator import add, divide


def test_add():
    assert add(2, 3) == 5


def test_divide_ok():
    assert divide(10, 2) == 5


def test_divide_by_zero_raises_value_error():
    with pytest.raises(ValueError):
        divide(10, 0)
