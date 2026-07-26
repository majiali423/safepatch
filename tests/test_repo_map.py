from pathlib import Path

from code_agent.repository.repo_map import build_repo_map


def test_build_repo_map(tmp_path: Path):
    (tmp_path / "calc.py").write_text(
        "class Calculator:\n"
        "    def divide(self, a, b):\n"
        "        return a / b\n"
        "\n"
        "def helper(x):\n"
        "    return x\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_calc.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )

    text, data = build_repo_map(tmp_path)
    assert "class Calculator" in text
    assert "divide(self, a, b)" in text
    assert "test_ok()" in text
    assert len(data["files"]) == 2
