from naming import format_name, initials
from user_service import display_user


def test_format_name_direct():
    assert format_name("Ada", "Lovelace") == "Ada Lovelace"


def test_display_user_order():
    assert display_user("Ada", "Lovelace") == "Ada Lovelace"


def test_initials():
    assert initials("Ada", "Lovelace") == "A.L."
