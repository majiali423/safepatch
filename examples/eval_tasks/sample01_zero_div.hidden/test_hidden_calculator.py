"""Hidden suite for sample01 — must not appear in the public task repo."""

import pytest

from calculator import divide

# Unique token used by leak-prevention tests (must never enter product artifacts).
HIDDEN_SAMPLE01_TOKEN = "HIDDEN_ONLY_ZERO_DIV_CASES_7f3a"


def test_hidden_divide_positive_zero():
    with pytest.raises(ValueError):
        divide(1, 0)


def test_hidden_divide_negative_zero():
    with pytest.raises(ValueError):
        divide(-42, 0)


def test_hidden_divide_ok_still_works():
    assert divide(9, 3) == 3
