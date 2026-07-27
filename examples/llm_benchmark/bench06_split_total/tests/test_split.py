from ledger import sum_shares
from split import split_equal


def test_split_sums_to_total():
    shares = split_equal(100, 3)
    assert sum(shares) == 100
    assert len(shares) == 3


def test_ledger_sums_all_shares():
    assert sum_shares([10, 20, 30]) == 60


def test_pipeline_split_then_ledger():
    shares = split_equal(100, 3)
    assert sum_shares(shares) == 100
