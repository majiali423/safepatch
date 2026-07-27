from web import slugify


def test_slugify_hello_world():
    # Intentionally narrow public coverage.
    assert slugify("Hello World") == "hello-world"
