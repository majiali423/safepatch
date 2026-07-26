from pathlib import Path

import pytest

from code_agent.repository.workspace import WorkspaceError, import_repository, safe_resolve


def test_import_and_safe_resolve(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("x", encoding="utf-8")

    imported = import_repository(repo, session_base=tmp_path / "sessions")
    assert (imported.workspace_root / "a.py").exists()
    assert not (imported.workspace_root / ".git").exists()

    p = safe_resolve(imported.workspace_root, "a.py")
    assert p.name == "a.py"

    with pytest.raises(WorkspaceError):
        safe_resolve(imported.workspace_root, "../a.py")
    with pytest.raises(WorkspaceError):
        safe_resolve(imported.workspace_root, str(repo / "a.py"))
