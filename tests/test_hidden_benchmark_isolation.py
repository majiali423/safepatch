"""Hidden benchmark evaluation must use an isolated eval copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_agent.eval.isolation import (
    isolated_eval_copy,
    workspace_content_fingerprint,
)
from code_agent.state import SessionStatus, TestResult
from examples.real_bug_benchmark.run_model_eval import (
    apply_patch_inside_workspace,
    run_hidden_evaluation,
)

HIDDEN_PATCH = (
    "diff --git a/safepatch_hidden/test_hidden_regression.py "
    "b/safepatch_hidden/test_hidden_regression.py\n"
    "new file mode 100644\n"
    "index 0000000..1111111\n"
    "--- /dev/null\n"
    "+++ b/safepatch_hidden/test_hidden_regression.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def test_hidden():\n"
    "+    assert True\n"
)


def _write_workspace(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_public.py").write_text(
        "def test_public():\n    assert True\n",
        encoding="utf-8",
    )
    return root


def _write_patch(path: Path, text: str = HIDDEN_PATCH) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class RecordingRunner:
    def __init__(
        self,
        *,
        result: TestResult | None = None,
        error: Exception | None = None,
        capture: list | None = None,
    ) -> None:
        self.result = result or TestResult(
            exit_code=0,
            stdout="1 passed",
            stderr="",
            duration_sec=0.01,
        )
        self.error = error
        self.capture = capture if capture is not None else []
        self.last_run_config = None

    def run_pytest(self, workspace_root, *, log_path=None, config=None):
        workspace = Path(workspace_root)
        self.capture.append(
            {
                "workspace": workspace,
                "exists_during_run": workspace.exists(),
                "has_hidden": (workspace / "safepatch_hidden").is_dir(),
                "hidden_files": sorted(
                    p.name for p in workspace.rglob("test_hidden_regression.py")
                ),
                "fingerprint": workspace_content_fingerprint(workspace)
                if workspace.exists()
                else None,
            }
        )
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text(
                f"exit_code: {self.result.exit_code}\n{self.result.stdout}\n",
                encoding="utf-8",
            )
        if self.error is not None:
            raise self.error
        return self.result


def test_isolated_eval_copy_is_removed_after_success(tmp_path: Path):
    workspace = _write_workspace(tmp_path / "working_copy")
    seen: list[Path] = []
    with isolated_eval_copy(workspace) as eval_copy:
        seen.append(eval_copy)
        assert eval_copy.exists()
        assert eval_copy != workspace
        (eval_copy / "marker.txt").write_text("only-in-copy\n", encoding="utf-8")
    assert seen[0].exists() is False
    assert not (workspace / "marker.txt").exists()


def test_isolated_eval_copy_is_removed_after_failure(tmp_path: Path):
    workspace = _write_workspace(tmp_path / "working_copy")
    seen: list[Path] = []
    with pytest.raises(RuntimeError, match="boom"):
        with isolated_eval_copy(workspace) as eval_copy:
            seen.append(eval_copy)
            raise RuntimeError("boom")
    assert seen[0].exists() is False
    assert workspace_content_fingerprint(workspace) == workspace_content_fingerprint(
        _write_workspace(tmp_path / "expected")
    )


def test_hidden_patch_only_appears_in_eval_copy(tmp_path: Path):
    workspace = _write_workspace(tmp_path / "working_copy")
    patch = _write_patch(tmp_path / "hidden.patch")
    log_path = tmp_path / "out" / "hidden_test.log"
    before = workspace_content_fingerprint(workspace)
    capture: list[dict] = []
    runner = RecordingRunner(capture=capture)

    result = run_hidden_evaluation(
        workspace_root=workspace,
        hidden_patch=patch,
        image="benchmark:test",
        hidden_command=["pytest", "-q", "safepatch_hidden"],
        log_path=log_path,
        runner=runner,  # type: ignore[arg-type]
    )

    assert result.exit_code == 0
    assert len(capture) == 1
    assert capture[0]["has_hidden"] is True
    assert capture[0]["hidden_files"] == ["test_hidden_regression.py"]
    assert capture[0]["workspace"] != workspace
    assert not (workspace / "safepatch_hidden").exists()
    assert list(workspace.rglob("*hidden*")) == []
    assert workspace_content_fingerprint(workspace) == before
    assert log_path.is_file()
    assert capture[0]["workspace"].exists() is False


def test_workspace_unchanged_when_hidden_tests_succeed(tmp_path: Path):
    workspace = _write_workspace(tmp_path / "working_copy")
    patch = _write_patch(tmp_path / "hidden.patch")
    before_files = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    before_fp = workspace_content_fingerprint(workspace)
    capture: list[dict] = []

    run_hidden_evaluation(
        workspace_root=workspace,
        hidden_patch=patch,
        image="benchmark:test",
        hidden_command=["pytest", "-q"],
        log_path=tmp_path / "hidden_test.log",
        runner=RecordingRunner(capture=capture),  # type: ignore[arg-type]
    )

    after_files = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files
    assert workspace_content_fingerprint(workspace) == before_fp
    assert capture[0]["workspace"].exists() is False


def test_eval_copy_removed_when_hidden_tests_fail(tmp_path: Path):
    workspace = _write_workspace(tmp_path / "working_copy")
    patch = _write_patch(tmp_path / "hidden.patch")
    before = workspace_content_fingerprint(workspace)
    capture: list[dict] = []
    runner = RecordingRunner(
        result=TestResult(
            exit_code=1,
            stdout="1 failed",
            stderr="",
            duration_sec=0.01,
        ),
        capture=capture,
    )

    result = run_hidden_evaluation(
        workspace_root=workspace,
        hidden_patch=patch,
        image="benchmark:test",
        hidden_command=["pytest", "-q"],
        log_path=tmp_path / "hidden_test.log",
        runner=runner,  # type: ignore[arg-type]
    )

    assert result.exit_code == 1
    assert capture[0]["has_hidden"] is True
    assert capture[0]["workspace"].exists() is False
    assert not (workspace / "safepatch_hidden").exists()
    assert workspace_content_fingerprint(workspace) == before


def test_eval_copy_removed_when_patch_injection_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace = _write_workspace(tmp_path / "working_copy")
    bad_patch = _write_patch(
        tmp_path / "bad.patch",
        "diff --git a/missing.py b/missing.py\n"
        "--- a/missing.py\n"
        "+++ b/missing.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
    )
    before = workspace_content_fingerprint(workspace)
    seen: list[Path] = []
    original_apply = apply_patch_inside_workspace

    def tracking_apply(target: Path, patch_path: Path) -> None:
        seen.append(target)
        assert (target / "mod.py").is_file()
        original_apply(target, patch_path)

    monkeypatch.setattr(
        "examples.real_bug_benchmark.run_model_eval.apply_patch_inside_workspace",
        tracking_apply,
    )

    with pytest.raises(Exception):
        run_hidden_evaluation(
            workspace_root=workspace,
            hidden_patch=bad_patch,
            image="benchmark:test",
            hidden_command=["pytest", "-q"],
            log_path=tmp_path / "hidden_test.log",
            runner=RecordingRunner(),  # type: ignore[arg-type]
        )

    assert seen
    assert seen[0].exists() is False
    assert not (workspace / "safepatch_hidden").exists()
    assert workspace_content_fingerprint(workspace) == before


def test_eval_copy_removed_when_docker_runner_raises(tmp_path: Path):
    workspace = _write_workspace(tmp_path / "working_copy")
    patch = _write_patch(tmp_path / "hidden.patch")
    before = workspace_content_fingerprint(workspace)
    capture: list[dict] = []
    runner = RecordingRunner(
        error=RuntimeError("docker blew up"),
        capture=capture,
    )

    with pytest.raises(RuntimeError, match="docker blew up"):
        run_hidden_evaluation(
            workspace_root=workspace,
            hidden_patch=patch,
            image="benchmark:test",
            hidden_command=["pytest", "-q"],
            log_path=tmp_path / "hidden_test.log",
            runner=runner,  # type: ignore[arg-type]
        )

    assert capture[0]["has_hidden"] is True
    assert capture[0]["workspace"].exists() is False
    assert not (workspace / "safepatch_hidden").exists()
    assert workspace_content_fingerprint(workspace) == before
    # Formal log evidence is retained outside the deleted eval copy.
    assert (tmp_path / "hidden_test.log").is_file()


def test_hidden_result_does_not_change_product_status(tmp_path: Path):
    workspace = _write_workspace(tmp_path / "working_copy")
    patch = _write_patch(tmp_path / "hidden.patch")
    product_status = SessionStatus.SUCCEEDED

    result = run_hidden_evaluation(
        workspace_root=workspace,
        hidden_patch=patch,
        image="benchmark:test",
        hidden_command=["pytest", "-q"],
        log_path=tmp_path / "hidden_test.log",
        runner=RecordingRunner(
            result=TestResult(
                exit_code=1,
                stdout="hidden failed",
                stderr="",
                duration_sec=0.01,
            )
        ),  # type: ignore[arg-type]
    )

    assert result.exit_code == 1
    # Hidden failure is recorded only in benchmark metrics by the caller; the
    # product SessionStatus object/value is not rewritten by the evaluator.
    assert product_status is SessionStatus.SUCCEEDED
    assert product_status.value == "SUCCEEDED"


def test_hidden_source_not_copied_into_session_artifacts(tmp_path: Path):
    workspace = _write_workspace(tmp_path / "working_copy")
    artifacts = tmp_path / "session" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "trace.jsonl").write_text("{}\n", encoding="utf-8")
    patch = _write_patch(tmp_path / "hidden.patch")

    run_hidden_evaluation(
        workspace_root=workspace,
        hidden_patch=patch,
        image="benchmark:test",
        hidden_command=["pytest", "-q"],
        log_path=tmp_path / "benchmark_out" / "hidden_test.log",
        runner=RecordingRunner(),  # type: ignore[arg-type]
    )

    assert list(artifacts.rglob("*hidden*")) == []
    assert not (workspace / "safepatch_hidden").exists()
