def validate_config(cfg: dict) -> dict:
    """Require name (non-empty str) and port (int 1..65535). Return normalized cfg."""
    # BUG B: does not coerce port to int / wrong range
    if 'name' not in cfg or cfg['name'] == '':
        raise ValueError('name required')
    if 'port' not in cfg:
        raise ValueError('port required')
    return {'name': cfg['name'], 'port': cfg['port']}
