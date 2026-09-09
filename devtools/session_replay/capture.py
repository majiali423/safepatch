"""Capture the nine deterministic controller scenarios into a new directory."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from code_agent.controller import TaskController
from code_agent.llm import LLMClient
from code_agent.state import TaskSession, TestResult
from devtools.session_replay.artifacts import write_manifest, write_v2_scenario
from devtools.session_replay.compare import check_invariants
from devtools.session_replay.constants import SCENARIO_NAMES
from devtools.session_replay.normalize import parse_trace, path_replacements


class FailThenPassRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_pytest(self, workspace_root: Path, *, log_path: Path | None = None) -> TestResult:
        self.calls += 1
        if self.calls == 1:
            result = TestResult(
                exit_code=1,
                stdout="FAILED tests/test_mod.py::test_f",
                stderr="",
                duration_sec=0.01,
                failed_tests=["tests/test_mod.py::test_f"],
            )
        else:
            result = TestResult(exit_code=0, stdout="1 passed", stderr="", duration_sec=0.01)
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(result.stdout, encoding="utf-8")
        return result


def _mini_repo(base: Path, *, extra_other: bool = False, conftest: str | None = None) -> Path:
    repo = base / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "from mod import f\ndef test_f():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    if extra_other:
        (repo / "other.py").write_text(
            "".join(f"value_{line} = {line}\n" for line in range(1, 501)),
            encoding="utf-8",
        )
    if conftest is not None:
        (repo / "conftest.py").write_text(conftest, encoding="utf-8")
    return repo


def _patch(old: str = "1", new: str = "2") -> dict[str, object]:
    return {
        "tool": "propose_patch",
        "args": {
            "diagnosis": "fix",
            "affected_files": ["mod.py"],
            "unified_diff": (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n"
                f"-    return {old}\n+    return {new}\n"
            ),
            "expected_behavior": f"return {new}",
            "risk_notes": "low",
            "tests_to_run": ["tests/test_mod.py"],
        },
    }


def _read(path: str = "mod.py") -> dict[str, object]:
    return {"tool": "read_file", "args": {"path": path, "start_line": 1, "end_line": 2}}


def _evidence(start: int, end: int) -> dict[str, object]:
    return {
        "tool": "request_evidence",
        "args": {
            "path": "other.py",
            "start_line": start,
            "end_line": end,
            "unanswered_requirement": "where the state is cleaned up",
            "reason": "existing evidence covers use but not cleanup",
        },
    }


def _replacements(tmp: Path, session: TaskSession) -> list[tuple[str, str]]:
    known = {
        str(tmp.resolve()): "<CAPTURE_ROOT>",
        str(session.session_dir.resolve()): "<SESSION_DIR>",
        str(session.workspace_root.resolve()): "<WORKSPACE>",
        str(session.artifacts_dir.resolve()): "<ARTIFACTS>",
        str(session.source_repo.resolve()): "<SOURCE_REPO>",
        session.session_id: "<SESSION>",
    }
    return path_replacements(known)


def _write_session(dest: Path, tmp: Path, session: TaskSession) -> dict[str, object]:
    artifacts = session.artifacts_dir
    summary = session.to_summary()
    records = parse_trace((artifacts / "trace.jsonl").read_text(encoding="utf-8"))
    diff_path = artifacts / "final.diff"
    final_diff = diff_path.read_text(encoding="utf-8") if diff_path.exists() else ""
    mod_path = session.workspace_root / "mod.py"
    workspace_mod = mod_path.read_text(encoding="utf-8") if mod_path.exists() else ""
    fingerprint = write_v2_scenario(
        dest,
        summary=summary,
        records=records,
        final_diff=final_diff,
        workspace_mod=workspace_mod,
        last_error=session.last_error or "",
        replacements=_replacements(tmp, session),
        extras={"pytest_scope_unsupported": session.pytest_scope_unsupported},
    )
    return fingerprint


def _llm(script: list[object]) -> LLMClient:
    return LLMClient(dry_run_script=script, model="gpt-4o-mini")


def _controller(tmp: Path, **kwargs: object) -> TaskController:
    return TaskController(session_base=tmp / "sessions", **kwargs)  # type: ignore[arg-type]


def capture_to(
    out_dir: Path,
    *,
    label: str = "current_working_tree_baseline",
) -> dict[str, dict[str, object]]:
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise SystemExit(f"refusing to overwrite existing path: {out_dir}")
    out_dir.mkdir(parents=True)
    results: dict[str, dict[str, object]] = {}
    with tempfile.TemporaryDirectory(prefix="safepatch-replay-") as raw:
        tmp_root = Path(raw)

        def execute(name: str, work: Path, repo: Path, **kwargs: object) -> dict[str, object]:
            session = _controller(work, **kwargs).run(repo, "make f return 2")
            return _write_session(out_dir / name, work, session)

        t = tmp_root / "success"
        t.mkdir()
        results["success"] = execute(
            "success",
            t,
            _mini_repo(t),
            llm=_llm([_patch()]),
            runner=FailThenPassRunner(),
            approve=lambda *_a, **_k: True,
        )

        t = tmp_root / "format_exhausted"
        t.mkdir()
        results["format_exhausted"] = execute(
            "format_exhausted",
            t,
            _mini_repo(t),
            llm=_llm(["bad1", "bad2", "bad3", _patch()]),
            runner=FailThenPassRunner(),
            approve=lambda *_a, **_k: True,
        )

        t = tmp_root / "evidence_then_patch"
        t.mkdir()
        results["evidence_then_patch"] = execute(
            "evidence_then_patch",
            t,
            _mini_repo(t, extra_other=True),
            llm=_llm([_read() for _ in range(12)] + [_evidence(360, 460), _patch()]),
            runner=FailThenPassRunner(),
            approve=lambda *_a, **_k: True,
        )

        t = tmp_root / "regen_exhausted"
        t.mkdir()
        mismatch = _patch("999", "2")
        results["regen_exhausted"] = execute(
            "regen_exhausted",
            t,
            _mini_repo(t),
            llm=_llm([mismatch, _read(), mismatch, _read(), mismatch]),
            runner=FailThenPassRunner(),
            approve=lambda *_a, **_k: True,
        )

        t = tmp_root / "rejected"
        t.mkdir()
        results["rejected"] = execute(
            "rejected",
            t,
            _mini_repo(t),
            llm=_llm([_patch()]),
            runner=FailThenPassRunner(),
            approve=lambda *_a, **_k: False,
        )

        t = tmp_root / "base_changed"
        t.mkdir()

        def mutate_and_approve(*_a: object, **_k: object) -> bool:
            for mod in (t / "sessions").rglob("mod.py"):
                if "working_copy" in mod.parts:
                    mod.write_text("def f():\n    return 1\n# mutated\n", encoding="utf-8")
                    break
            return True

        results["base_changed"] = execute(
            "base_changed",
            t,
            _mini_repo(t),
            llm=_llm([_patch()]),
            runner=FailThenPassRunner(),
            approve=mutate_and_approve,
        )

        t = tmp_root / "cancelled"
        t.mkdir()
        llm = _llm([_patch()])

        def boom(_messages: object) -> object:
            raise KeyboardInterrupt

        llm.complete = boom  # type: ignore[method-assign]
        try:
            execute(
                "cancelled",
                t,
                _mini_repo(t),
                llm=llm,
                runner=FailThenPassRunner(),
                approve=lambda *_a, **_k: True,
            )
            results["cancelled"] = {"status": "ERROR", "stop_reason": "uncancelled"}
        except KeyboardInterrupt:
            sessions = list((t / "sessions").glob("*"))
            if not sessions:
                results["cancelled"] = {"status": "MISSING", "stop_reason": "missing"}
            else:
                session_dir = sessions[0]
                artifacts = session_dir / "artifacts"
                summary = json.loads((artifacts / "summary.json").read_text(encoding="utf-8"))
                records = parse_trace((artifacts / "trace.jsonl").read_text(encoding="utf-8"))
                diff_path = artifacts / "final.diff"
                final_diff = diff_path.read_text(encoding="utf-8") if diff_path.exists() else ""
                workspace = session_dir / "working_copy"
                mod_path = workspace / "mod.py"
                workspace_mod = mod_path.read_text(encoding="utf-8") if mod_path.exists() else ""
                known = {
                    str(t.resolve()): "<CAPTURE_ROOT>",
                    str(session_dir.resolve()): "<SESSION_DIR>",
                    str(workspace.resolve()): "<WORKSPACE>",
                    str(artifacts.resolve()): "<ARTIFACTS>",
                    str((t / "repo").resolve()): "<SOURCE_REPO>",
                    session_dir.name: "<SESSION>",
                }
                last_error = "SESSION_CANCELLED: interrupted by the operator"
                fp = write_v2_scenario(
                    out_dir / "cancelled",
                    summary=summary,
                    records=records,
                    final_diff=final_diff,
                    workspace_mod=workspace_mod,
                    last_error=last_error,
                    replacements=path_replacements(known),
                    extras={"pytest_scope_unsupported": False},
                )
                results["cancelled"] = fp

        t = tmp_root / "deadline_after_model"
        t.mkdir()
        llm = _llm([_patch()])
        clock = {"t": 0.0}
        orig = llm.complete

        def late(messages: object) -> object:
            clock["t"] = 10.0
            return orig(messages)

        llm.complete = late  # type: ignore[method-assign]
        results["deadline_after_model"] = execute(
            "deadline_after_model",
            t,
            _mini_repo(t),
            llm=llm,
            runner=FailThenPassRunner(),
            approve=lambda *_a, **_k: True,
            session_deadline_seconds=1.0,
            monotonic=lambda: clock["t"],
        )

        t = tmp_root / "unsupported_pytest"
        t.mkdir()
        results["unsupported_pytest"] = execute(
            "unsupported_pytest",
            t,
            _mini_repo(
                t,
                conftest="import pytest\n@pytest.fixture\ndef value():\n    return 1\n",
            ),
            llm=_llm([_patch()]),
            runner=FailThenPassRunner(),
            approve=lambda *_a, **_k: True,
        )

    write_manifest(out_dir, label=label)
    missing = [name for name in SCENARIO_NAMES if name not in results]
    if missing:
        raise SystemExit(f"capture did not write scenarios: {missing}")
    invariant_errors = check_invariants(results)
    if invariant_errors:
        raise SystemExit("capture invariant failed:\n" + "\n".join(invariant_errors))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture nine deterministic SafePatch scenarios (refuses existing --out)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New directory to create. Existing paths are rejected.",
    )
    parser.add_argument(
        "--label",
        default="current_working_tree_baseline",
        help="Manifest label; default marks a current-version baseline, not pre-refactor evidence.",
    )
    args = parser.parse_args(argv)
    capture_to(args.out, label=args.label)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
