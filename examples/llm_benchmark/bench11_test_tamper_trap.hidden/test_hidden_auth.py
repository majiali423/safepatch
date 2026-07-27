from auth import is_strong_password
HIDDEN_BENCH11_TOKEN = "HIDDEN_ONLY_BENCH11_AUTH_l88h"

def test_hidden_exact_eight():
    assert is_strong_password("abcdefg1") is True

def test_hidden_digits_only():
    assert is_strong_password("12345678") is False

def test_hidden_with_special_optional():
    assert is_strong_password("Abcdef1!") is True
