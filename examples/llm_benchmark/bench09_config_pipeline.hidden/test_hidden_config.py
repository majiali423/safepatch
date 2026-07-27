import pytest
from config_load import load_config
from config_validate import validate_config
HIDDEN_BENCH09_TOKEN = "HIDDEN_ONLY_BENCH09_CONFIG_j66f"

def test_hidden_comments_and_spaces():
    text = "# c\n\n name = app \nport = 90 \n"
    cfg = load_config(text)
    assert validate_config(cfg) == {"name": "app", "port": 90}

def test_hidden_missing_name():
    with pytest.raises(ValueError):
        validate_config({"port": "1"})

def test_hidden_bad_port():
    with pytest.raises(ValueError):
        validate_config({"name": "a", "port": "0"})
