from seq import unique_keep_order
HIDDEN_BENCH03_TOKEN = "HIDDEN_ONLY_BENCH03_UNIQUE_f22b"

def test_hidden_strings():
    assert unique_keep_order(["a", "b", "a", "c"]) == ["a", "b", "c"]

def test_hidden_all_same():
    assert unique_keep_order([9, 9, 9]) == [9]

def test_hidden_already_unique():
    assert unique_keep_order([3, 1, 2]) == [3, 1, 2]
