from __future__ import annotations

from pathlib import Path

from code_agent.patching.applier import apply_proposal
from code_agent.repository.git_diff import (
    unified_diff_between,
    write_unified_diff,
)
from code_agent.state import PatchProposal


def _write_bytes(path: Path, text: str, newline: str) -> None:
    body = newline.join(text.split("\n"))
    if not body.endswith(("\n", "\r")):
        body += newline
    path.write_bytes(body.encode("utf-8"))


def test_final_diff_no_blank_line_noise_for_crlf_and_lf(tmp_path: Path):
    for label, nl in (("lf", "\n"), ("crlf", "\r\n")):
        orig = tmp_path / f"orig_{label}"
        curr = tmp_path / f"curr_{label}"
        orig.mkdir()
        curr.mkdir()
        src = (
            "def divide(a, b):\n"
            "    return a / b\n"
        )
        dst = (
            "def divide(a, b):\n"
            "    if b == 0:\n"
            "        raise ValueError('x')\n"
            "    return a / b\n"
        )
        _write_bytes(orig / "calculator.py", src, nl)
        _write_bytes(curr / "calculator.py", dst, nl)

        diff = unified_diff_between(orig, curr)
        assert "\r" not in diff
        # No blank-line noise: never two consecutive empty lines in the hunk body.
        assert "\n\n\n" not in diff
        lines = diff.splitlines()
        assert lines[0] == "--- a/calculator.py"
        assert lines[1] == "+++ b/calculator.py"
        assert any(line.startswith("+    if b == 0:") for line in lines)
        assert any(line.startswith("+        raise ValueError") for line in lines)
        # Context/added lines should not be separated by empty lines only.
        plus_idx = next(i for i, line in enumerate(lines) if line.startswith("+    if b == 0:"))
        assert lines[plus_idx + 1].startswith("+        raise ValueError")

        out = tmp_path / f"final_{label}.diff"
        write_unified_diff(out, diff)
        raw = out.read_bytes()
        assert b"\r" not in raw
        assert b"\n\n\n" not in raw


def test_apply_patch_preserves_crlf(tmp_path: Path):
    path = tmp_path / "a.py"
    _write_bytes(path, "def f():\n    return 1\n", "\r\n")
    proposal = PatchProposal(
        diagnosis="d",
        affected_files=["a.py"],
        unified_diff=(
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def f():\n"
            "+    # note\n"
            "     return 1\n"
        ),
        expected_behavior="e",
        risk_notes="r",
        tests_to_run=["t"],
    )
    result = apply_proposal(proposal, tmp_path)
    assert result.ok, result.error
    data = path.read_bytes()
    assert b"\r\n" in data
    assert b"# note" in data
    # New line should use CRLF, not bare LF sandwiched oddly.
    assert b"# note\r\n" in data


def test_apply_patch_preserves_lf(tmp_path: Path):
    path = tmp_path / "a.py"
    _write_bytes(path, "def f():\n    return 1\n", "\n")
    proposal = PatchProposal(
        diagnosis="d",
        affected_files=["a.py"],
        unified_diff=(
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def f():\n"
            "+    # note\n"
            "     return 1\n"
        ),
        expected_behavior="e",
        risk_notes="r",
        tests_to_run=["t"],
    )
    result = apply_proposal(proposal, tmp_path)
    assert result.ok, result.error
    data = path.read_bytes()
    assert b"\r\n" not in data
    assert b"# note\n" in data
