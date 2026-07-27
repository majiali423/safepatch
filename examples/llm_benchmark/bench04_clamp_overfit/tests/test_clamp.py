from bounds import clamp

def test_inside():
    assert clamp(5, 0, 10) == 5

def test_below():
    assert clamp(-1, 0, 10) == 0
