from web import slugify

HIDDEN_BENCH12_TOKEN = "HIDDEN_ONLY_BENCH12_SLUG_d03a"


def test_hidden_punctuation():
    assert slugify("Hello, World!") == "hello-world"


def test_hidden_multi_space_and_dash():
    assert slugify("  Foo   --  Bar  ") == "foo-bar"


def test_hidden_empty():
    assert slugify("") == ""
    assert slugify("@@@") == ""
