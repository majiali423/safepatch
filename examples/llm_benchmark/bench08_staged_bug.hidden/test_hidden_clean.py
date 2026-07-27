from csvkit import clean_row
HIDDEN_BENCH08_TOKEN = "HIDDEN_ONLY_BENCH08_CLEAN_i55e"

def test_hidden_keep_zero_string():
    assert clean_row({"n": "0"}) == {"n": "0"}

def test_hidden_combined():
    assert clean_row({"  a  ": "  ", "b": 2}) == {"a": None, "b": 2}
