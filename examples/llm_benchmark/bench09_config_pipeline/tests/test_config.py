import pytest

from config_load import load_config
from config_validate import validate_config


def test_pipeline_strips_keys_and_coerces_port():
    # Requires BOTH load (strip / ignore junk) AND validate (int port).
    text = "name = demo\nport = 8080\n"
    assert validate_config(load_config(text)) == {"name": "demo", "port": 8080}


def test_validate_port_range():
    with pytest.raises(ValueError):
        validate_config({"name": "x", "port": "99999"})
