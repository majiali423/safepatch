from csvkit import clean_row

def test_strip_values():
    assert clean_row({"n": "  a  "}) == {"n": "a"}

def test_strip_keys():
    assert clean_row({"  n  ": "x"}) == {"n": "x"}

def test_empty_to_none():
    assert clean_row({"n": ""}) == {"n": None}
