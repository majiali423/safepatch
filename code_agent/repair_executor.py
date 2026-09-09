"""Apply an approved patch, classify failures, and run isolated pytest."""

from __future__ import annotations

from dataclasses import dataclass

from code_agent.patching.applier import classify_apply_error_kind, is_recoverable_apply_error
from code_agent.state import AttemptRecord, SessionStatus, TaskSession
from code_agent.tracing.recorder import TraceRecorder
from code_agent.workflow import PatchExecutionService, VerificationPolicy


@dataclass
class RepairCycleResult:
    terminal: bool
    reanalyze: bool


class RepairExecutor:
    def __init__(
        self,
        *,
        patch_execution: PatchExecutionService,
        runner: object,
        say: object,
        docker_config_payload: object,
        register_apply_failure_read: object,
        consume_regeneration: object,
        regeneration_exhausted: object,
        preflight_enabled: bool,
    ) -> None:
        self.patch_execution = patch_execution
        self.runner = runner
        self.say = say
        self._docker_config_payload = docker_config_payload
        self._register_apply_failure_read = register_apply_failure_read
        self._consume_regeneration = consume_regeneration
        self._regeneration_exhausted = regeneration_exhausted
        self.preflight_enabled = preflight_enabled

    def execute(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        binding,
        next_attempt_no: int,
    ) -> RepairCycleResult:
        proposal = binding.proposal
        apply_result = self.patch_execution.apply(proposal, session.workspace_root)
        if not apply_result.ok:
            return self._handle_apply_failure(
                session, trace, binding, apply_result, next_attempt_no
            )
        return self._run_tests(session, trace, binding, apply_result)

    def _handle_apply_failure(
        self, session, trace, binding, apply_result, next_attempt_no
    ) -> RepairCycleResult:
        if self.preflight_enabled:
            session.patch_apply_failures_after_preflight += 1
            event = "patch_apply_failed_after_preflight"
            feedback_label = "PATCH_APPLY_FAILED_AFTER_PREFLIGHT"
            default_error = "apply failed after preflight"
        else:
            session.patch_apply_failures_without_preflight += 1
            event = "patch_apply_failed_without_preflight"
            feedback_label = "PATCH_APPLY_FAILED"
            default_error = "patch apply failed"
        err = apply_result.error or default_error
        error_kind = classify_apply_error_kind(
            error_kind=apply_result.error_kind,
            error=err,
        )
        rollback_succeeded = apply_result.rollback_succeeded
        if rollback_succeeded is None:
            rollback_succeeded = True
        trace.emit(
            event,
            attempt=next_attempt_no,
            error=err,
            error_kind=error_kind,
            target_file=apply_result.target_file,
            rollback_succeeded=rollback_succeeded,
            patch_hash=binding.patch_hash,
            working_tree_hash=binding.working_tree_hash,
        )
        self.say(f"Patch apply failed ({error_kind}): {err}")
        session.attempts.append(
            AttemptRecord(
                attempt=next_attempt_no,
                proposal=binding.proposal,
                approved=True,
                apply_ok=False,
                apply_error=err,
                patch_hash=binding.patch_hash,
                working_tree_hash=binding.working_tree_hash,
            )
        )
        if rollback_succeeded is False:
            session.status = SessionStatus.ERROR
            session.stop_reason = "patch_rollback_failed"
            session.last_error = f"{error_kind}: {err}"
            return RepairCycleResult(terminal=True, reanalyze=False)
        if error_kind == "base_changed":
            session.status = SessionStatus.PATCH_BASE_CHANGED
            session.stop_reason = "patch_base_changed"
            session.last_error = err
            return RepairCycleResult(terminal=True, reanalyze=False)
        if not is_recoverable_apply_error(error_kind):
            session.status = SessionStatus.ERROR
            session.stop_reason = f"patch_apply_{error_kind}"
            session.last_error = f"{error_kind}: {err}"
            return RepairCycleResult(terminal=True, reanalyze=False)
        session.last_apply_feedback = (
            f"{feedback_label}:\n"
            f"error_kind={error_kind}\n"
            f"{err}\n"
            "Re-read the target file and propose_patch again with an "
            "exact unified diff for the current working tree."
        )
        if self._regeneration_exhausted(session):
            session.status = SessionStatus.PATCH_NOT_APPLICABLE
            session.stop_reason = "patch_not_applicable"
            session.last_error = err
            return RepairCycleResult(terminal=True, reanalyze=False)
        self._register_apply_failure_read(
            session,
            trace,
            apply_result.target_file,
            apply_result.line_no,
            error_kind,
        )
        self._consume_regeneration(session, trace, reason=err)
        session.status = SessionStatus.ANALYZING
        return RepairCycleResult(terminal=False, reanalyze=True)

    def _run_tests(self, session, trace, binding, apply_result) -> RepairCycleResult:
        session.attempts_used += 1
        session.consecutive_patch_regeneration_retries = 0
        session.last_apply_feedback = ""
        attempt = AttemptRecord(
            attempt=session.attempts_used,
            proposal=binding.proposal,
            approved=True,
            apply_ok=True,
            patch_hash=binding.patch_hash,
            working_tree_hash=binding.working_tree_hash,
        )
        session.status = SessionStatus.PATCH_APPLIED
        session.changed_files = sorted(
            set(session.changed_files) | set(apply_result.files or [])
        )
        session.evidence.invalidate_paths(apply_result.files or [])
        trace.emit(
            "patch_applied",
            attempt=session.attempts_used,
            ok=True,
            files=apply_result.files,
            patch_hash=binding.patch_hash,
            working_tree_hash=binding.working_tree_hash,
        )
        session.status = SessionStatus.TESTING
        self.say(f"Running Docker pytest (attempt {session.attempts_used})...")
        session.observability.post_apply_pytest_runs += 1
        test_result = self.runner.run_pytest(
            session.workspace_root,
            log_path=session.artifacts_dir / f"attempt-{session.attempts_used}.log",
        )
        attempt.test_result = test_result
        session.attempts.append(attempt)
        session.consecutive_patch_regeneration_retries = 0
        trace.emit(
            "pytest_finished",
            attempt=session.attempts_used,
            result=test_result.to_dict(),
            **self._docker_config_payload(),
        )
        if test_result.environment_error:
            if test_result.error_kind == "timeout":
                session.status = SessionStatus.TEST_TIMEOUT
                session.stop_reason = "pytest_timeout"
            else:
                session.status = SessionStatus.TEST_ENVIRONMENT_ERROR
                session.stop_reason = "test_environment_error"
            session.last_error = test_result.environment_error
            return RepairCycleResult(terminal=True, reanalyze=False)
        if test_result.passed:
            status, stop_reason, message = VerificationPolicy.passing_status(
                session.baseline
            )
            session.status = status
            session.stop_reason = stop_reason
            self.say(message)
            return RepairCycleResult(terminal=True, reanalyze=False)
        self.say(
            f"Tests failed ({len(test_result.failed_tests)}). "
            f"Attempts left: {session.max_attempts - session.attempts_used}"
        )
        if session.attempts_used >= session.max_attempts:
            session.status = SessionStatus.FAILED_MAX_ATTEMPTS
            session.stop_reason = "max_patch_attempts_reached"
            return RepairCycleResult(terminal=True, reanalyze=False)
        session.status = SessionStatus.ANALYZING
        return RepairCycleResult(terminal=False, reanalyze=True)
