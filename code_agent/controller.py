from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from code_agent.analysis_loop import AnalysisLoop
from code_agent.llm import ModelProvider
from code_agent.patch_gate import PatchGate
from code_agent.patching.applier import apply_proposal
from code_agent.patching.hashes import patch_hash, working_tree_hash
from code_agent.patching.test_collection import (
    InventoryCompleteness,
    ProtectedTestInventory,
    TestInventoryReport,
    build_protected_inventory,
)
from code_agent.patching.validator import PolicyValidator
from code_agent.repair_executor import RepairExecutor
from code_agent.repository.git_diff import snapshot_tree
from code_agent.repository.repo_map import build_repo_map
from code_agent.repository.workspace import ImportedWorkspace, import_repository
from code_agent.runtime.docker_pytest import DockerPytestRunner
from code_agent.runtime.protocol import TestRunner
from code_agent.session_control import RegenerationBudget, RequiredReadTracker, SessionDeadline
from code_agent.session_finalizer import SessionFinalizer
from code_agent.state import (
    TERMINAL_STATUSES,
    ApprovalBinding,
    AttemptRecord,
    SessionStatus,
    TaskSession,
    TestResult,
)
from code_agent.tracing.recorder import TraceRecorder
from code_agent.workflow import ApprovalGate, PatchExecutionService

ApprovalFn = Callable[[ApprovalBinding, int], bool]
MessageFn = Callable[[str], None]


class TaskController:
    def __init__(
        self,
        *,
        llm: ModelProvider,
        runner: TestRunner | None = None,
        approve: ApprovalFn | None = None,
        say: MessageFn | None = None,
        session_base: Path | None = None,
        max_analysis_steps: int = 20,
        allow_test_changes: bool = False,
        allow_new_tests: bool = True,
        policy: PolicyValidator | None = None,
        preflight_enabled: bool = True,
        wall_time: Callable[[], float] | None = None,
        monotonic: Callable[[], float] | None = None,
        session_deadline_seconds: float | None = None,
    ) -> None:
        self.llm = llm
        self.runner = runner or DockerPytestRunner()
        self.approve = approve
        self.approval_gate = ApprovalGate(approve)
        self.say = say or (lambda _m: None)
        self.session_base = session_base
        self.max_analysis_steps = max_analysis_steps
        self.allow_test_changes = allow_test_changes
        self.allow_new_tests = allow_new_tests
        self.policy = policy or PolicyValidator(
            allow_test_changes=allow_test_changes,
            allow_new_tests=allow_new_tests,
        )
        self.preflight_enabled = preflight_enabled
        self.patch_execution = PatchExecutionService(
            allow_test_changes=allow_test_changes,
            allow_new_tests=allow_new_tests,
            apply_fn=apply_proposal,
        )
        self._wall_time = wall_time or time.time
        self._monotonic = monotonic or time.perf_counter
        self.session_deadline_seconds = session_deadline_seconds
        self.deadline = SessionDeadline(
            monotonic=self._monotonic,
            session_deadline_seconds=session_deadline_seconds,
        )
        self.required_reads = RequiredReadTracker()
        self.regeneration = RegenerationBudget()
        self.finalizer = SessionFinalizer(
            runner=self.runner,
            llm=self.llm,
            wall_time=self._wall_time,
            monotonic=self._monotonic,
        )
        self.patch_gate = PatchGate(preflight_enabled=preflight_enabled, say=self.say)
        self.analysis_loop = AnalysisLoop(
            llm=self.llm,
            policy=self.policy,
            patch_gate=self.patch_gate,
            say=self.say,
            deadline=self.deadline,
            usage=self.finalizer,
            required_reads=self.required_reads,
            regeneration=self.regeneration,
            max_analysis_steps=max_analysis_steps,
        )
        self.repair_executor = RepairExecutor(
            patch_execution=self.patch_execution,
            runner=self.runner,
            say=self.say,
            docker_config_payload=self._docker_config_payload,
            register_apply_failure_read=self.required_reads.require_apply_failure_region_read,
            consume_regeneration=self.regeneration.consume,
            regeneration_exhausted=self.regeneration.exhausted,
            preflight_enabled=preflight_enabled,
        )

    def run(self, repo_path: Path, bug_description: str) -> TaskSession:
        imported = import_repository(repo_path, self.session_base)
        session = TaskSession(
            session_id=imported.session_id,
            source_repo=Path(repo_path).resolve(),
            session_dir=imported.session_dir,
            workspace_root=imported.workspace_root,
            artifacts_dir=imported.artifacts_dir,
            bug_description=bug_description,
            status=SessionStatus.CREATED,
        )
        self.finalizer.init_observability(session)
        self.deadline.bind_start(session.observability._mono_start)
        extra_secrets = tuple(
            value
            for value in (getattr(self.llm, "api_key", None),)
            if isinstance(value, str) and value
        )
        trace = TraceRecorder(
            session.artifacts_dir / "trace.jsonl",
            extra_secrets=extra_secrets,
        )
        trace.emit(
            "session_created",
            session_id=session.session_id,
            source_repo=str(session.source_repo),
        )

        snapshot_root = session.session_dir / "baseline_snapshot"
        try:
            self._import_phase(session, imported, trace, snapshot_root)
            self._baseline_phase(session, trace)
            if session.status in {
                SessionStatus.TEST_ENVIRONMENT_ERROR,
                SessionStatus.TEST_TIMEOUT,
            }:
                self._finalize(session, trace, snapshot_root)
                return session
            if self._stop_unsupported_pytest_scope(session, trace):
                self._finalize(session, trace, snapshot_root)
                return session

            while (
                session.status not in TERMINAL_STATUSES
                and session.attempts_used < session.max_attempts
            ):
                binding = self.analysis_loop.run(session, trace, snapshot_root)
                if session.status in TERMINAL_STATUSES:
                    break
                if binding is None:
                    if session.status not in TERMINAL_STATUSES:
                        session.status = SessionStatus.ERROR
                        session.stop_reason = "agent_finished_without_patch"
                        session.last_error = "Agent finished without proposing a patch"
                    break

                if self.deadline.abort_if_deadline(session, "after_proposal"):
                    break

                proposal = binding.proposal
                validation = binding.validation
                session.current_proposal = proposal

                session.status = SessionStatus.AWAITING_APPROVAL
                next_attempt_no = session.attempts_used + 1
                approval = self.approval_gate.decide(binding, next_attempt_no)
                if self.deadline.abort_if_deadline(session, "after_proposal"):
                    break
                if approval.decision == "handler_missing":
                    session.status = SessionStatus.REJECTED
                    session.stop_reason = approval.stop_reason or "approval_handler_missing"
                    session.last_error = "No approval handler was provided; patch was not applied"
                    trace.emit(
                        "approval_decision",
                        attempt=next_attempt_no,
                        decision="handler_missing",
                        stop_reason=session.stop_reason,
                        patch_hash=binding.patch_hash,
                        working_tree_hash=binding.working_tree_hash,
                    )
                    break
                approved = approval.approved
                trace.emit(
                    "approval_decision",
                    attempt=next_attempt_no,
                    decision=approval.decision,
                    patch_hash=binding.patch_hash,
                    working_tree_hash=binding.working_tree_hash,
                    high_risk=validation.high_risk,
                    new_test_files=validation.new_test_files,
                    modified_test_files=validation.modified_test_files,
                )
                if not approved:
                    session.status = SessionStatus.REJECTED
                    session.stop_reason = "user_rejected_patch"
                    session.attempts.append(
                        AttemptRecord(
                            attempt=next_attempt_no,
                            proposal=proposal,
                            approved=False,
                            patch_hash=binding.patch_hash,
                            working_tree_hash=binding.working_tree_hash,
                        )
                    )
                    break

                if self.deadline.abort_if_deadline(session, "after_approval"):
                    break

                # Approval bound to hashes — reject if working tree moved.
                current_wt = working_tree_hash(session.workspace_root)
                current_ph = patch_hash(proposal.unified_diff)
                if current_wt != binding.working_tree_hash or current_ph != binding.patch_hash:
                    session.status = SessionStatus.PATCH_BASE_CHANGED
                    session.stop_reason = "patch_base_changed"
                    session.last_error = (
                        "Working tree or patch changed after approval "
                        f"(bound_wt={binding.working_tree_hash}, now_wt={current_wt}, "
                        f"bound_patch={binding.patch_hash}, now_patch={current_ph})"
                    )
                    session.attempts.append(
                        AttemptRecord(
                            attempt=next_attempt_no,
                            proposal=proposal,
                            approved=True,
                            apply_ok=False,
                            apply_error=session.last_error,
                            patch_hash=binding.patch_hash,
                            working_tree_hash=binding.working_tree_hash,
                        )
                    )
                    break

                if self.deadline.abort_if_deadline(session, "before_apply"):
                    break

                outcome = self.repair_executor.execute(
                    session, trace, binding, next_attempt_no
                )
                if outcome.terminal:
                    break
                if outcome.reanalyze:
                    continue

        except KeyboardInterrupt:
            session.status = SessionStatus.ERROR
            session.stop_reason = "cancelled"
            session.last_error = "SESSION_CANCELLED: interrupted by the operator"
            self.finalizer.finalize(session, trace, snapshot_root, self.say)
            raise
        except Exception as exc:
            session.status = SessionStatus.ERROR
            session.stop_reason = "internal_error"
            session.last_error = str(exc)
            self.finalizer.finish_observability(session)
            trace.emit("session_finished", status=session.status.value, error=str(exc))
            self.finalizer.write_artifacts(session, snapshot_root)
            raise

        self.finalizer.finalize(session, trace, snapshot_root, self.say)
        return session

    def _import_phase(
        self,
        session: TaskSession,
        imported: ImportedWorkspace,
        trace: TraceRecorder,
        snapshot_root: Path,
    ) -> None:
        session.status = SessionStatus.IMPORTED
        trace.emit(
            "repository_imported",
            workspace=str(session.workspace_root),
            file_count=imported.file_count,
            total_bytes=imported.total_bytes,
        )
        self.say(f"Imported repository -> {session.workspace_root}")

        text, data = build_repo_map(session.workspace_root)
        session.repo_map_text = text
        session.repo_map_data = data
        (session.artifacts_dir / "repo_map.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        trace.emit("repo_map_generated", file_count=len(data.get("files", [])))
        snapshot_tree(session.workspace_root, snapshot_root)
        inventory = build_protected_inventory(session.workspace_root)
        self._bind_test_inventory(session, inventory, trace, stage="import")

    def _bind_test_inventory(
        self,
        session: TaskSession,
        inventory: ProtectedTestInventory,
        trace: TraceRecorder,
        *,
        stage: str,
    ) -> None:
        collected = inventory.files
        self.policy.collected_test_files = collected
        self.patch_execution.collected_test_files = collected
        session.pytest_scope_unsupported = bool(inventory.unsupported_scope)
        session.pytest_protection_reasons = list(inventory.reasons)
        trace.emit(
            "test_files_collected",
            stage=stage,
            count=len(collected),
            fail_closed=inventory.fail_closed,
            unsupported_scope=inventory.unsupported_scope,
            completeness=inventory.completeness.value,
            reasons=inventory.reasons[:12],
            files=sorted(collected)[:80],
        )

    def _baseline_phase(self, session: TaskSession, trace: TraceRecorder) -> None:
        self.say("Running baseline pytest in Docker...")
        session.observability.baseline_pytest_runs += 1
        result = self.runner.run_pytest(
            session.workspace_root,
            log_path=session.artifacts_dir / "baseline.log",
        )
        session.baseline = result
        trace.emit(
            "baseline_test_finished",
            result=result.to_dict(),
            **self._docker_config_payload(),
        )
        if result.environment_error:
            if result.error_kind == "timeout":
                session.status = SessionStatus.TEST_TIMEOUT
                session.stop_reason = "pytest_timeout"
            else:
                session.status = SessionStatus.TEST_ENVIRONMENT_ERROR
                session.stop_reason = "test_environment_error"
            session.last_error = result.environment_error
            self.say(result.environment_error)
            return
        inventory = build_protected_inventory(
            session.workspace_root,
            isolated_nodeids=_log_extract_nodeids(result),
            report=_inventory_report_from_result(result),
        )
        self._bind_test_inventory(session, inventory, trace, stage="baseline")
        session.status = SessionStatus.BASELINE_TESTED
        self.say(
            f"Baseline finished: exit={result.exit_code}, failed={result.failed_tests or 'none'}"
        )

    def _stop_unsupported_pytest_scope(
        self, session: TaskSession, trace: TraceRecorder
    ) -> bool:
        if not session.pytest_scope_unsupported:
            return False
        detail = "; ".join(session.pytest_protection_reasons) or "unknown pytest collection"
        session.status = SessionStatus.PATCH_NOT_APPLICABLE
        session.stop_reason = "unsupported_pytest_collection"
        session.last_error = (
            "Unsupported pytest configuration; SafePatch will not modify this "
            f"repository: {detail}"
        )
        trace.emit(
            "pytest_scope_unsupported",
            reasons=session.pytest_protection_reasons[:12],
            stop_reason=session.stop_reason,
        )
        self.say(session.last_error)
        return True

    def _finalize(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        snapshot_root: Path,
    ) -> None:
        self.finalizer.finalize(session, trace, snapshot_root, self.say)

    def _docker_config_payload(self) -> dict[str, object]:
        return self.finalizer.docker_config_payload()


def _inventory_report_from_result(result: TestResult) -> TestInventoryReport:
    inventory = getattr(result, "test_inventory", None)
    if inventory is not None:
        return inventory
    return TestInventoryReport(
        completeness=InventoryCompleteness.MISSING,
        source="absent",
    )


def _log_extract_nodeids(result: TestResult) -> list[str] | None:
    inventory = getattr(result, "test_inventory", None)
    if (
        inventory is not None
        and inventory.completeness is not InventoryCompleteness.MISSING
    ):
        return None
    nodeids: list[str] = []
    nodeids.extend(result.collected_test_files or [])
    nodeids.extend(result.failed_tests or [])
    return nodeids or None
