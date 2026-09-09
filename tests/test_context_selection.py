from __future__ import annotations

from pathlib import Path

from code_agent.repository.context_selector import select_context_files
from code_agent.repository.repo_map import build_repo_map


def test_context_selector_ranks_traceback_file(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("import helper\nVALUE = 1\n", encoding="utf-8")
    (repo / "helper.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "unrelated.py").write_text("Y = 2\n", encoding="utf-8")
    _text, data = build_repo_map(repo)
    ranked = select_context_files(
        workspace_root=repo,
        repo_map_data=data,
        traceback_summary='File "mod.py", line 1, in test\nNameError',
        bug_description="fix VALUE",
    )
    assert ranked[0] == "mod.py"
    assert "helper.py" in ranked
