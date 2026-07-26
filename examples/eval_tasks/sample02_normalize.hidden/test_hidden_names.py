"""Hidden suite for sample02 — catches hard-coded public-only patches."""

from names import normalize_name

# Unique token used by leak-prevention tests (must never enter product artifacts).
HIDDEN_SAMPLE02_TOKEN = "HIDDEN_ONLY_NORMALIZE_BOB_MARY_9c2e"


def test_hidden_normalize_bob():
    assert normalize_name(" BOB ") == "bob"


def test_hidden_normalize_empty():
    assert normalize_name("") == ""


def test_hidden_normalize_mary_jane():
    assert normalize_name("  Mary Jane  ") == "mary jane"
