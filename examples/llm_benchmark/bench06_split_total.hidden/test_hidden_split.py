from ledger import sum_shares
from split import split_equal

HIDDEN_BENCH06_TOKEN = "HIDDEN_ONLY_BENCH06_SPLIT_c77f"


def test_hidden_split_remainder():
    assert split_equal(10, 3) == [4, 3, 3]
    assert sum(split_equal(10, 3)) == 10


def test_hidden_ledger_single():
    assert sum_shares([7]) == 7


def test_hidden_pipeline():
    assert sum_shares(split_equal(11, 4)) == 11
