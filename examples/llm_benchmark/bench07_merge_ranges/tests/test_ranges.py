from ranges import merge_ranges

def test_unsorted_overlap():
    assert merge_ranges([(2, 4), (1, 3)]) == [(1, 4)]
