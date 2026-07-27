from pricing import final_price


def test_ten_percent_off():
    assert final_price(100, 0.1) == 90
