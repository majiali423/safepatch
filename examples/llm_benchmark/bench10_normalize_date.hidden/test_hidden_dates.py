import pytest
from dates import normalize_date
HIDDEN_BENCH10_TOKEN = "HIDDEN_ONLY_BENCH10_DATE_k77g"

def test_hidden_iso():
    assert normalize_date("2024-03-09") == "2024-03-09"

def test_hidden_leap():
    assert normalize_date("2024/2/29") == "2024-02-29"

def test_hidden_bad_month():
    with pytest.raises(ValueError):
        normalize_date("2024-13-01")
