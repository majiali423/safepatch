import pytest
from parsing import parse_int
HIDDEN_BENCH02_TOKEN = "HIDDEN_ONLY_BENCH02_PARSE_e11a"

def test_hidden_tabs():
    assert parse_int("\t7\n") == 7

def test_hidden_spaces_only():
    with pytest.raises(ValueError):
        parse_int("   ")

def test_hidden_negative():
    assert parse_int(" -3 ") == -3
