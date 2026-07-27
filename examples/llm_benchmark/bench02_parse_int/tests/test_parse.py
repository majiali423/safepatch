import pytest
from parsing import parse_int

def test_parse_with_spaces():
    assert parse_int(" 42 ") == 42

def test_empty_raises():
    with pytest.raises(ValueError):
        parse_int("")
