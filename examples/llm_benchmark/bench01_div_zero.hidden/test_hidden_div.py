import pytest

from mathutil import safe_div

# Leak marker — must never appear in product artifacts.
HIDDEN_BENCH01_TOKEN = "HIDDEN_ONLY_BENCH01_DIV_ZERO_a91c"


def test_hidden_negative_over_zero():
    with pytest.raises(ValueError):
        safe_div(-42, 0)


def test_hidden_zero_over_zero():
    with pytest.raises(ValueError):
        safe_div(0, 0)


def test_hidden_float_ok():
    assert safe_div(7.5, 2.5) == 3.0
