from seq import unique_keep_order

def test_basic():
    assert unique_keep_order([1, 2, 1]) == [1, 2]

def test_empty():
    assert unique_keep_order([]) == []
