from pathlib import Path

from code_agent.patching.applier import apply_proposal
from code_agent.patching.validator import validate_proposal
from code_agent.state import PatchProposal


def _proposal(diff: str, files: list[str] | None = None) -> PatchProposal:
    return PatchProposal(
        diagnosis="fix divide zero",
        affected_files=files or ["calculator.py"],
        unified_diff=diff,
        expected_behavior="raise ValueError",
        risk_notes="low",
        tests_to_run=["tests/test_calculator.py"],
    )


def test_apply_valid_patch(tmp_path: Path):
    (tmp_path / "calculator.py").write_text(
        "def divide(a, b):\n"
        "    return a / b\n",
        encoding="utf-8",
    )
    diff = (
        "--- a/calculator.py\n"
        "+++ b/calculator.py\n"
        "@@ -1,2 +1,4 @@\n"
        " def divide(a, b):\n"
        "+    if b == 0:\n"
        "+        raise ValueError('division by zero')\n"
        "     return a / b\n"
    )
    proposal = _proposal(diff)
    assert validate_proposal(proposal, tmp_path).ok
    result = apply_proposal(proposal, tmp_path)
    assert result.ok, result.error
    text = (tmp_path / "calculator.py").read_text(encoding="utf-8")
    assert "ValueError" in text


def test_reject_delete_and_requirements(tmp_path: Path):
    (tmp_path / "calculator.py").write_text("x=1\n", encoding="utf-8")
    delete_diff = (
        "--- a/calculator.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-x=1\n"
    )
    v = validate_proposal(_proposal(delete_diff), tmp_path)
    assert not v.ok
    assert any("Deleting" in e for e in v.errors)

    req_diff = (
        "--- a/requirements.txt\n"
        "+++ b/requirements.txt\n"
        "@@ -0,0 +1 @@\n"
        "+pytest\n"
    )
    # path isn't .py either
    p = _proposal(req_diff, files=["requirements.txt"])
    v2 = validate_proposal(p, tmp_path)
    assert not v2.ok
