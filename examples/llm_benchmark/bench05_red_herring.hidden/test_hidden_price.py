from pricing import final_price

HIDDEN_BENCH05_TOKEN = "HIDDEN_ONLY_BENCH05_RED_HERRING_b42e"


def test_hidden_rate_zero():
    assert final_price(50, 0) == 50


def test_hidden_rate_one():
    assert final_price(80, 1) == 0


def test_hidden_fractional():
    assert final_price(200, 0.25) == 150
