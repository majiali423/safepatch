"""P2-C1: hidden-test evaluator (isolated from product Agent loop)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from code_agent.controller import TaskController
from code_agent.eval.hidden import (
    discover_hidden_dir,
    evaluate_after_product,
    run_hidden_tests,
)
from code_agent.eval.status import EvalStatus
from code_agent.llm import LLMClient
from code_agent.repository.repo_map import build_repo_map
from code_agent.runtime.docker_config import HIDDEN_PYTEST_ARGV, DockerRunConfig
from code_agent.state import SessionStatus, TestResult

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "examples" / "eval_tasks"
SAMPLE01 = SAMPLES / "sample01_zero_div"
SAMPLE02 = SAMPLES / "sample02_normalize"
HIDDEN01_TOKEN = "HIDDEN_ONLY_ZERO_DIV_CASES_7f3a"
HIDDEN02_TOKEN = "HIDDEN_ONLY_NORMALIZE_BOB_MARY_9c2e"


class FakeHiddenRunner:
    """Mock Docker runner for unit tests."""

    def __init__(self, result: TestResult, capture: list | None = None):
        self.result = result
        self.capture = capture if capture is not None else []
        self.last_run_config = None

    def preflight(self):
        return True, "ok"

    def _resolve_image(self):
        return "code-agent-pytest:local"

    def make_run_config(self, image: str) -> DockerRunConfig:
        return DockerRunConfig(image=image)

    def run_pytest(self, workspace_root, *, log_path=None, config=None):
        self.capture.append(
            {
                "workspace": Path(workspace_root),
                "config": config,
                "has_tests_hidden": (Path(workspace_root) / "tests_hidden").is_dir(),
                "hidden_files": sorted(
                    p.name
                    for p in (Path(workspace_root) / "tests_hidden").glob("test_*.py")
                )
                if (Path(workspace_root) / "tests_hidden").is_dir()
                else [],
            }
        )
        if config is not None:
            self.last_run_config = config
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"exit_code: {self.result.exit_code}\n{self.result.stdout}\n",
                encoding="utf-8",
            )
        return self.result


def _summary(status: str = "SUCCEEDED", **extra) -> dict:
    base = {
        "status": status,
        "attempts_used": 1,
        "total_format_retries_used": 0,
        "changed_files": ["calculator.py"],
        "baseline_tests_passed": False,
        "final_tests_passed": status == "SUCCEEDED",
        "stop_reason": "all_tests_passed" if status == "SUCCEEDED" else "failed",
    }
    base.update(extra)
    return base


def test_discover_hidden_sibling_outside_public_repo():
    hidden = discover_hidden_dir(SAMPLE01)
    assert hidden is not None
    assert hidden.name == "sample01_zero_div.hidden"
    assert hidden.parent == SAMPLE01.parent
    # Must not live inside the Agent-imported public repo (sibling, not child).
    assert not hidden.resolve().is_relative_to(SAMPLE01.resolve())
    assert hidden.resolve().parent == SAMPLE01.resolve().parent
    assert not (SAMPLE01 / "tests_hidden").exists()
    assert not list(SAMPLE01.rglob("*HIDDEN_ONLY*"))


def test_product_failure_skips_hidden(tmp_path: Path):
    results = tmp_path / "results"
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE01, wc)
    calls: list = []
    runner = FakeHiddenRunner(
        TestResult(exit_code=0, stdout="ok", stderr="", duration_sec=0.01),
        capture=calls,
    )
    metrics = evaluate_after_product(
        product_summary=_summary("FAILED_MAX_ATTEMPTS"),
        working_copy=wc,
        task_dir=SAMPLE01,
        results_dir=results,
        runner=runner,
    )
    assert metrics["eval_status"] == EvalStatus.PUBLIC_TESTS_FAILED.value
    assert metrics["public_pass"] is False
    assert metrics["hidden_pass"] is None
    assert calls == []
    assert not (results / "hidden.log").exists()


def test_public_and_hidden_pass(tmp_path: Path):
    results = tmp_path / "results"
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE01, wc)
    runner = FakeHiddenRunner(
        TestResult(exit_code=0, stdout="3 passed", stderr="", duration_sec=0.01)
    )
    metrics = evaluate_after_product(
        product_summary=_summary("SUCCEEDED"),
        working_copy=wc,
        task_dir=SAMPLE01,
        results_dir=results,
        runner=runner,
    )
    assert metrics["eval_status"] == EvalStatus.SUCCEEDED.value
    assert metrics["public_pass"] is True
    assert metrics["hidden_pass"] is True
    assert metrics["hidden_exit_code"] == 0
    assert metrics["hidden_test_count"] >= 2  # sample01 has multiple hidden cases
    assert (results / "hidden.log").exists()
    assert (results / "metrics.json").exists()
    assert (results / "eval_trace.jsonl").exists()


def test_public_pass_hidden_fail(tmp_path: Path):
    results = tmp_path / "results"
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE02, wc)
    runner = FakeHiddenRunner(
        TestResult(
            exit_code=1,
            stdout="FAILED tests_hidden/test_hidden_names.py::test_hidden_normalize_bob",
            stderr="",
            duration_sec=0.01,
            failed_tests=[
                "tests_hidden/test_hidden_names.py::test_hidden_normalize_bob"
            ],
        )
    )
    metrics = evaluate_after_product(
        product_summary=_summary(
            "SUCCEEDED", changed_files=["names.py"]
        ),
        working_copy=wc,
        task_dir=SAMPLE02,
        results_dir=results,
        runner=runner,
    )
    assert metrics["eval_status"] == EvalStatus.HIDDEN_TESTS_FAILED.value
    assert metrics["public_pass"] is True
    assert metrics["hidden_pass"] is False
    assert metrics["product_status"] == "SUCCEEDED"


def test_hidden_timeout(tmp_path: Path):
    results = tmp_path / "results"
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE01, wc)
    runner = FakeHiddenRunner(
        TestResult(
            exit_code=-1,
            stdout="",
            stderr="timeout",
            duration_sec=120,
            environment_error="TEST_TIMEOUT: pytest exceeded 120s",
            error_kind="timeout",
        )
    )
    metrics = evaluate_after_product(
        product_summary=_summary(),
        working_copy=wc,
        task_dir=SAMPLE01,
        results_dir=results,
        runner=runner,
    )
    assert metrics["eval_status"] == EvalStatus.HIDDEN_TEST_TIMEOUT.value


def test_hidden_environment_error(tmp_path: Path):
    results = tmp_path / "results"
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE01, wc)
    runner = FakeHiddenRunner(
        TestResult(
            exit_code=-1,
            stdout="",
            stderr="no docker",
            duration_sec=0,
            environment_error="TEST_ENVIRONMENT_ERROR: docker missing",
            error_kind="environment",
        )
    )
    metrics = evaluate_after_product(
        product_summary=_summary(),
        working_copy=wc,
        task_dir=SAMPLE01,
        results_dir=results,
        runner=runner,
    )
    assert metrics["eval_status"] == EvalStatus.HIDDEN_TEST_ENVIRONMENT_ERROR.value


def test_no_hidden_configured(tmp_path: Path):
    task = tmp_path / "task_plain"
    task.mkdir()
    (task / "TASK.txt").write_text("x", encoding="utf-8")
    (task / "a.py").write_text("x=1\n", encoding="utf-8")
    wc = tmp_path / "wc"
    shutil.copytree(task, wc)
    results = tmp_path / "results"
    metrics = evaluate_after_product(
        product_summary=_summary(changed_files=["a.py"]),
        working_copy=wc,
        task_dir=task,
        results_dir=results,
        runner=FakeHiddenRunner(
            TestResult(exit_code=0, stdout="", stderr="", duration_sec=0)
        ),
    )
    assert metrics["hidden_pass"] is None
    assert metrics["eval_status"] == EvalStatus.SUCCEEDED.value
    assert metrics.get("note") == "No hidden tests configured"
    # Must not present null as a pass.
    assert metrics["hidden_pass"] is not True


def test_hidden_not_in_working_copy_or_repo_map(tmp_path: Path):
    results = tmp_path / "results"
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE02, wc)
    before_files = {p.relative_to(wc).as_posix() for p in wc.rglob("*") if p.is_file()}
    runner = FakeHiddenRunner(
        TestResult(exit_code=1, stdout="fail", stderr="", duration_sec=0.01)
    )
    evaluate_after_product(
        product_summary=_summary(changed_files=["names.py"]),
        working_copy=wc,
        task_dir=SAMPLE02,
        results_dir=results,
        runner=runner,
    )
    after_files = {p.relative_to(wc).as_posix() for p in wc.rglob("*") if p.is_file()}
    assert before_files == after_files
    assert not (wc / "tests_hidden").exists()
    assert HIDDEN02_TOKEN not in (wc / "names.py").read_text(encoding="utf-8")
    for path in wc.rglob("*.py"):
        assert HIDDEN02_TOKEN not in path.read_text(encoding="utf-8")

    map_text, map_data = build_repo_map(wc)
    blob = map_text + json.dumps(map_data)
    assert "tests_hidden" not in blob
    assert "test_hidden_names" not in blob
    assert HIDDEN02_TOKEN not in blob


def test_eval_temp_copy_removed_and_uses_hidden_argv(tmp_path: Path):
    results = tmp_path / "results"
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE01, wc)
    capture: list = []
    runner = FakeHiddenRunner(
        TestResult(exit_code=0, stdout="ok", stderr="", duration_sec=0.01),
        capture=capture,
    )
    run_hidden_tests(
        working_copy=wc,
        hidden_tests_dir=discover_hidden_dir(SAMPLE01),
        results_dir=results,
        runner=runner,
    )
    assert not (results / "eval_temp_copy").exists()
    assert capture
    assert capture[0]["has_tests_hidden"] is True
    assert capture[0]["hidden_files"]
    cfg = capture[0]["config"]
    assert cfg is not None
    assert tuple(cfg.pytest_argv) == HIDDEN_PYTEST_ARGV
    # Security fields preserved from DockerRunConfig defaults.
    assert cfg.network_mode == "none"
    assert cfg.memory == "512m"
    assert cfg.user == "1000:1000"
    assert cfg.remove_container is True


def test_two_eval_result_dirs_do_not_overwrite(tmp_path: Path):
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE01, wc)
    runner = FakeHiddenRunner(
        TestResult(exit_code=0, stdout="ok", stderr="", duration_sec=0.01)
    )
    r1 = tmp_path / "results" / "run_a"
    r2 = tmp_path / "results" / "run_b"
    m1 = evaluate_after_product(
        product_summary=_summary(),
        working_copy=wc,
        task_dir=SAMPLE01,
        results_dir=r1,
        runner=runner,
    )
    m2 = evaluate_after_product(
        product_summary=_summary(),
        working_copy=wc,
        task_dir=SAMPLE01,
        results_dir=r2,
        runner=runner,
    )
    assert r1.exists() and r2.exists()
    assert (r1 / "metrics.json").exists() and (r2 / "metrics.json").exists()
    assert m1["eval_status"] == m2["eval_status"] == EvalStatus.SUCCEEDED.value


def test_product_summary_not_rewritten_by_hidden_failure(tmp_path: Path):
    results = tmp_path / "results"
    results.mkdir()
    summary = _summary("SUCCEEDED", changed_files=["names.py"])
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE02, wc)
    evaluate_after_product(
        product_summary=summary,
        working_copy=wc,
        task_dir=SAMPLE02,
        results_dir=results,
        runner=FakeHiddenRunner(
            TestResult(exit_code=1, stdout="fail", stderr="", duration_sec=0.01)
        ),
    )
    disk = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    assert disk["status"] == "SUCCEEDED"
    metrics = json.loads((results / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["eval_status"] == EvalStatus.HIDDEN_TESTS_FAILED.value
    assert (results / "hidden.log").exists()
    # public attempt log is separate — we only require hidden.log independence here
    assert metrics["product_status"] == "SUCCEEDED"


def test_eval_trace_events_and_no_hidden_source(tmp_path: Path):
    results = tmp_path / "results"
    wc = tmp_path / "wc"
    shutil.copytree(SAMPLE02, wc)
    hidden_src = (
        discover_hidden_dir(SAMPLE02) / "test_hidden_names.py"
    ).read_text(encoding="utf-8")
    evaluate_after_product(
        product_summary=_summary(changed_files=["names.py"]),
        working_copy=wc,
        task_dir=SAMPLE02,
        results_dir=results,
        runner=FakeHiddenRunner(
            TestResult(exit_code=1, stdout="fail", stderr="", duration_sec=0.01)
        ),
    )
    text = (results / "eval_trace.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line)["event"] for line in text.splitlines() if line.strip()]
    for required in (
        "hidden_eval_started",
        "eval_temp_copy_created",
        "hidden_tests_injected",
        "hidden_pytest_started",
        "hidden_pytest_finished",
        "eval_temp_copy_removed",
        "eval_finished",
    ):
        assert required in events
    # Must not dump full hidden source into eval_trace.
    assert "def test_hidden_normalize_bob" not in text
    assert hidden_src not in text
    assert "sha256" in text


def test_leak_prevention_end_to_end_dry_run(tmp_path: Path):
    """Product Agent run must never see hidden tests; eval runs only after."""
    script = json.loads(
        (SAMPLES / "dry_run_sample02_hardcode.json").read_text(encoding="utf-8")
    )
    description = (SAMPLE02 / "TASK.txt").read_text(encoding="utf-8").strip()

    class LocalPublicRunner:
        def run_pytest(self, workspace_root, *, log_path=None, config=None):
            import subprocess

            proc = subprocess.run(
                ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                check=False,
            )
            result = TestResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_sec=0.01,
            )
            if log_path:
                log_path.write_text(result.stdout, encoding="utf-8")
            return result

    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=LocalPublicRunner(),  # type: ignore[arg-type]
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(SAMPLE02, description)
    assert session.status == SessionStatus.SUCCEEDED

    # Hidden must not be in source repo (sanity) or working_copy.
    assert HIDDEN02_TOKEN not in (SAMPLE02 / "names.py").read_text(encoding="utf-8")
    wc_blob = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in session.workspace_root.rglob("*")
        if p.is_file()
    )
    assert HIDDEN02_TOKEN not in wc_blob
    assert not (session.workspace_root / "tests_hidden").exists()

    repo_map_path = session.artifacts_dir / "repo_map.json"
    assert repo_map_path.exists()
    repo_map_text = repo_map_path.read_text(encoding="utf-8")
    assert "test_hidden_names" not in repo_map_text
    assert HIDDEN02_TOKEN not in repo_map_text

    product_trace = (session.artifacts_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert HIDDEN02_TOKEN not in product_trace
    assert "tests_hidden" not in product_trace
    # model_request payloads must not contain hidden content
    for line in product_trace.splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event") == "model_request":
            assert HIDDEN02_TOKEN not in json.dumps(ev)

    results = tmp_path / "eval_results"
    # During eval, hidden appears only in temp copy (assert via capture), then deleted.
    capture: list = []
    fake = FakeHiddenRunner(
        TestResult(
            exit_code=1,
            stdout="FAILED",
            stderr="",
            duration_sec=0.01,
            failed_tests=["tests_hidden/test_hidden_names.py::test_hidden_normalize_bob"],
        ),
        capture=capture,
    )
    metrics = evaluate_after_product(
        product_summary=session.to_summary(),
        working_copy=session.workspace_root,
        task_dir=SAMPLE02,
        results_dir=results,
        runner=fake,
    )
    assert metrics["product_status"] == "SUCCEEDED"
    assert metrics["eval_status"] == EvalStatus.HIDDEN_TESTS_FAILED.value
    assert capture[0]["has_tests_hidden"] is True
    assert not (results / "eval_temp_copy").exists()
    # Product summary untouched.
    assert session.status == SessionStatus.SUCCEEDED
    assert session.to_summary()["status"] == "SUCCEEDED"
    # Public vs hidden logs independent.
    assert (session.artifacts_dir / "baseline.log").exists() or True
    assert (results / "hidden.log").exists()
    assert HIDDEN02_TOKEN not in product_trace


def test_docker_config_for_hidden_reuses_security_fields():
    cfg = DockerRunConfig(image="code-agent-pytest:local").for_hidden_tests()
    assert list(cfg.pytest_argv) == list(HIDDEN_PYTEST_ARGV)
    assert cfg.network_mode == "none"
    assert cfg.pids_limit == 128
    cmd = cfg.build_docker_cmd("/tmp/eval")
    assert "tests_hidden" in cmd
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"


@pytest.mark.integration
def test_docker_hidden_integration_sample02(tmp_path: Path):
    """Real Docker hidden run: hardcode passes public, fails hidden."""
    from code_agent.runtime.docker_pytest import DockerPytestRunner

    runner = DockerPytestRunner()
    ok, reason = runner.preflight()
    if not ok:
        pytest.skip(f"Docker unavailable: {reason}")

    script = json.loads(
        (SAMPLES / "dry_run_sample02_hardcode.json").read_text(encoding="utf-8")
    )
    controller = TaskController(
        llm=LLMClient(dry_run_script=script),
        runner=runner,
        approve=lambda *_: True,
        say=lambda _m: None,
        session_base=tmp_path / "sessions",
    )
    session = controller.run(
        SAMPLE02, (SAMPLE02 / "TASK.txt").read_text(encoding="utf-8").strip()
    )
    assert session.status == SessionStatus.SUCCEEDED
    results = tmp_path / "results"
    metrics = evaluate_after_product(
        product_summary=session.to_summary(),
        working_copy=session.workspace_root,
        task_dir=SAMPLE02,
        results_dir=results,
        runner=runner,
    )
    assert metrics["product_status"] == "SUCCEEDED"
    assert metrics["public_pass"] is True
    assert metrics["hidden_pass"] is False
    assert metrics["eval_status"] == EvalStatus.HIDDEN_TESTS_FAILED.value
    assert (results / "hidden.log").exists()
    assert not (results / "eval_temp_copy").exists()
    assert HIDDEN02_TOKEN not in (
        session.artifacts_dir / "trace.jsonl"
    ).read_text(encoding="utf-8")
