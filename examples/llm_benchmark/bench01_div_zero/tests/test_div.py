import pytest

from mathutil import safe_div
from ops import safe_div as safe_div_ops


def test_divide_ok():
    assert safe_div(10, 2) == 5
    assert safe_div_ops(9, 3) == 3


def test_divide_by_zero_raises_value_error():
    with pytest.raises(ValueError):
        safe_div(10, 0)
