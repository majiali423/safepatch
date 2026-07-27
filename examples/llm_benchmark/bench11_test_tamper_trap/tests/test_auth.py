from auth import is_strong_password

def test_ok_example():
    assert is_strong_password("Abcdef12") is True

def test_too_short():
    assert is_strong_password("Ab1") is False

def test_letters_only():
    assert is_strong_password("abcdefgh") is False
