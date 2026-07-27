def load_config(text: str) -> dict[str, str]:
    """Parse key=value lines. Ignore blank lines and # comments."""
    # BUG A: does not ignore comments / blank lines correctly (keeps them / no strip)
    cfg = {}
    for line in text.splitlines():
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        cfg[k] = v
    return cfg
