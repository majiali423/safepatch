import pytest
from bounds import clamp
HIDDEN_BENCH04_TOKEN = "HIDDEN_ONLY_BENCH04_CLAMP_g33c"

def test_hidden_above():
    assert clamp(11, 0, 10) == 10

def test_hidden_equal_bounds():
    assert clamp(3, 5, 5) == 5

def test_hidden_lo_gt_hi():
    with pytest.raises(ValueError):
        clamp(1, 5, 2)
