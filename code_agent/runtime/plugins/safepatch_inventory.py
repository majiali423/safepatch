"""Controlled pytest plugin loaded only via /opt/safepatch/run_pytest.py.

This module records collection in-process. It does not write a report under
``/work``: that tree is target-writable (same uid as tests) and cannot prove
plugin origin. Host-side protection must not treat workspace JSON as trusted.
"""

from __future__ import annotations

MAX_FILES = 500
MAX_NODEIDS = 2000


def pytest_configure(config) -> None:
    config._safepatch_inventory = {
        "complete": False,
        "truncated": False,
        "files": [],
        "nodeids": [],
    }
    config._safepatch_did_collect = False


def pytest_collection_finish(session) -> None:
    config = session.config
    files: list[str] = []
    nodeids: list[str] = []
    seen: set[str] = set()
    truncated = False
    for item in session.items:
        nodeid = str(getattr(item, "nodeid", "") or "")
        if nodeid:
            nodeids.append(nodeid)
        path = getattr(item, "path", None)
        if path is None:
            path = getattr(item, "fspath", None)
        text = str(path) if path is not None else ""
        if text and text not in seen:
            seen.add(text)
            files.append(text)
        if len(files) > MAX_FILES or len(nodeids) > MAX_NODEIDS:
            truncated = True
            break
    config._safepatch_inventory["files"] = files[:MAX_FILES]
    config._safepatch_inventory["nodeids"] = nodeids[:MAX_NODEIDS]
    config._safepatch_inventory["truncated"] = truncated
    config._safepatch_did_collect = not truncated
