import pytest
from dates import normalize_date

def test_slash_pad():
    assert normalize_date("2024/1/2") == "2024-01-02"

def test_invalid_format():
    with pytest.raises(ValueError):
        normalize_date("2024.01.02")
