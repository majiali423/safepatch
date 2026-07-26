from inventory import available, can_fulfill


def test_available_basic():
    assert available(10, 3) == 7


def test_available_never_negative():
    # reserved may exceed stock due to oversell; clamp to 0
    assert available(5, 8) == 0


def test_can_fulfill():
    assert can_fulfill(10, 3, 7) is True
    assert can_fulfill(10, 3, 8) is False
