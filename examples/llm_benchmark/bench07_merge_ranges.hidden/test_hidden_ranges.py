from ranges import merge_ranges
HIDDEN_BENCH07_TOKEN = "HIDDEN_ONLY_BENCH07_RANGES_h44d"

def test_hidden_unsorted():
    assert merge_ranges([(5, 6), (1, 2), (2, 4)]) == [(1, 6)]

def test_hidden_touching():
    assert merge_ranges([(1, 2), (3, 4)]) == [(1, 4)]

def test_hidden_empty_and_single():
    assert merge_ranges([]) == []
    assert merge_ranges([(8, 9)]) == [(8, 9)]
