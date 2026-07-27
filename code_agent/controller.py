from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from code_agent.llm import (
    FORMAT_RETRY_HINT,
    LLMClient,
    LLMError,
    ModelOutputError,
    raw_preview,
)
from code_agent.patching.applier import (
    apply_proposal,
    classify_apply_error_kind,
    is_recoverable_apply_error,
)
from code_agent.patching.hashes import patch_hash, working_tree_hash
from code_agent.patching.preflight import PatchPreflight, PreflightFailure
from code_agent.patching.proposal import parse_proposal
from code_agent.patching.validator import PolicyValidator, ValidationResult
from code_agent.repository.git_diff import (
    changed_files_from_diff,
    current_diff_from_snapshot,
    snapshot_tree,
    write_unified_diff,
)
from code_agent.repository.repo_map import build_repo_map
from code_agent.repository.workspace import import_repository
from code_agent.runtime.docker_pytest import DockerPytestRunner
from code_agent.state import (
    TERMINAL_STATUSES,
    ApprovalBinding,
    AttemptRecord,
    PatchProposal,
    SessionStatus,
    TaskSession,
    TokenUsage,
)
from code_agent.tools.registry import (
    READ_TOOLS,
    ToolError,
    ToolRegistry,
    execute_tool,
)
from code_agent.tracing.recorder import TraceRecorder

ApprovalFn = Callable[[ApprovalBinding, int], bool]
MessageFn = Callable[[str], None]
# Inspection tools counted as read_tool_calls (budgeted READ_TOOLS + get_current_diff).
_READ_LIKE_TOOLS = READ_TOOLS | {"get_current_diff"}


class TaskController:
    def __init__(
        self,
        *,
        llm: LLMClient,
        runner: DockerPytestRunner | None = None,
        approve: ApprovalFn | None = None,
        say: MessageFn | None = None,
        session_base: Path | None = None,
        max_analysis_steps: int = 20,
        allow_test_changes: bool = False,
        allow_new_tests: bool = True,
        policy: PolicyValidator | None = None,
        wall_time: Callable[[], float] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.llm = llm
        self.runner = runner or DockerPytestRunner()
        self.approve = approve or (lambda _binding, _i: True)
        self.say = say or (lambda _m: None)
        self.session_base = session_base
        self.max_analysis_steps = max_analysis_steps
        self.allow_test_changes = allow_test_changes
        self.allow_new_tests = allow_new_tests
        self.policy = policy or PolicyValidator(
            allow_test_changes=allow_test_changes,
            allow_new_tests=allow_new_tests,
        )
        self._wall_time = wall_time or time.time
        self._monotonic = monotonic or time.perf_counter

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
        self._init_observability(session)
        trace = TraceRecorder(session.artifacts_dir / "trace.jsonl")
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

            while (
                session.status not in TERMINAL_STATUSES
                and session.attempts_used < session.max_attempts
            ):
                binding = self._analyze_phase(session, trace, snapshot_root)
                if session.status in TERMINAL_STATUSES:
                    break
                if binding is None:
                    if session.status not in TERMINAL_STATUSES:
                        session.status = SessionStatus.ERROR
                        session.stop_reason = "agent_finished_without_patch"
                        session.last_error = "Agent finished without proposing a patch"
                    break

                proposal = binding.proposal
                validation = binding.validation
                session.current_proposal = proposal

                session.status = SessionStatus.AWAITING_APPROVAL
                next_attempt_no = session.attempts_used + 1
                approved = self.approve(binding, next_attempt_no)
                trace.emit(
                    "approval_decision",
                    attempt=next_attempt_no,
                    decision="approve" if approved else "reject",
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

                # Approval bound to hashes — reject if working tree moved.
                current_wt = working_tree_hash(session.workspace_root)
                current_ph = patch_hash(proposal.unified_diff)
                if (
                    current_wt != binding.working_tree_hash
                    or current_ph != binding.patch_hash
                ):
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

                apply_result = apply_proposal(
                    proposal,
                    session.workspace_root,
                    allow_test_changes=self.allow_test_changes,
                    allow_new_tests=self.allow_new_tests,
                )
                if not apply_result.ok:
                    session.patch_apply_failures_after_preflight += 1
                    err = apply_result.error or "apply failed after preflight"
                    error_kind = classify_apply_error_kind(
                        error_kind=apply_result.error_kind,
                        error=err,
                    )
                    rollback_succeeded = apply_result.rollback_succeeded
                    if rollback_succeeded is None:
                        rollback_succeeded = True
                    trace.emit(
                        "patch_apply_failed_after_preflight",
                        attempt=next_attempt_no,
                        error=err,
                        error_kind=error_kind,
                        target_file=apply_result.target_file,
                        rollback_succeeded=rollback_succeeded,
                        patch_hash=binding.patch_hash,
                        working_tree_hash=binding.working_tree_hash,
                    )
                    self.say(
                        f"Patch apply failed after preflight ({error_kind}): {err}"
                    )
                    session.attempts.append(
                        AttemptRecord(
                            attempt=next_attempt_no,
                            proposal=proposal,
                            approved=True,
                            apply_ok=False,
                            apply_error=err,
                            patch_hash=binding.patch_hash,
                            working_tree_hash=binding.working_tree_hash,
                        )
                    )

                    if error_kind == "base_changed":
                        session.status = SessionStatus.PATCH_BASE_CHANGED
                        session.stop_reason = "patch_base_changed"
                        session.last_error = err
                        break

                    if not is_recoverable_apply_error(error_kind):
                        # I/O / permission / internal: no regen, no repair, no pytest.
                        session.status = SessionStatus.ERROR
                        session.stop_reason = f"patch_apply_{error_kind}"
                        session.last_error = f"{error_kind}: {err}"
                        break

                    session.last_apply_feedback = (
                        "PATCH_APPLY_FAILED_AFTER_PREFLIGHT:\n"
                        f"error_kind={error_kind}\n"
                        f"{err}\n"
                        "Re-read the target file and propose_patch again with an "
                        "exact unified diff for the current working tree."
                    )
                    if self._regeneration_exhausted(session):
                        session.status = SessionStatus.PATCH_NOT_APPLICABLE
                        session.stop_reason = "patch_not_applicable"
                        session.last_error = err
                        break
                    self._consume_regeneration(session, trace, reason=err)
                    session.status = SessionStatus.ANALYZING
                    continue

                # Repair attempt starts only after successful exact apply.
                session.attempts_used += 1
                # Regeneration budget resets only after a successful apply.
                session.consecutive_patch_regeneration_retries = 0
                session.last_apply_feedback = ""
                attempt = AttemptRecord(
                    attempt=session.attempts_used,
                    proposal=proposal,
                    approved=True,
                    apply_ok=True,
                    patch_hash=binding.patch_hash,
                    working_tree_hash=binding.working_tree_hash,
                )

                session.status = SessionStatus.PATCH_APPLIED
                session.changed_files = sorted(
                    set(session.changed_files) | set(apply_result.files or [])
                )
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
                # Next proposal window gets a fresh regeneration budget.
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
                    break

                if test_result.passed:
                    session.status = SessionStatus.SUCCEEDED
                    session.stop_reason = "all_tests_passed"
                    self.say("All tests passed.")
                    break

                self.say(
                    f"Tests failed ({len(test_result.failed_tests)}). "
                    f"Attempts left: {session.max_attempts - session.attempts_used}"
                )
                if session.attempts_used >= session.max_attempts:
                    session.status = SessionStatus.FAILED_MAX_ATTEMPTS
                    session.stop_reason = "max_patch_attempts_reached"
                    break
                session.status = SessionStatus.ANALYZING

        except Exception as exc:
            session.status = SessionStatus.ERROR
            session.stop_reason = "internal_error"
            session.last_error = str(exc)
            self._finish_observability(session)
            trace.emit("session_finished", status=session.status.value, error=str(exc))
            self._write_artifacts(session, snapshot_root)
            raise

        self._finalize(session, trace, snapshot_root)
        return session

    def _init_observability(self, session: TaskSession) -> None:
        obs = session.observability
        obs.start(wall_time=self._wall_time, monotonic=self._monotonic)
        obs.model_provider = self.llm.provider_name
        obs.model_name = self.llm.model
        obs.tool_calling_protocol = self.llm.tool_calling_protocol
        obs.temperature = getattr(self.llm, "temperature", None)
        # Count provider attempts at invocation start (inside LLMClient._fetch_raw).
        self.llm.on_provider_invocation = obs.note_provider_invocation

    def _finish_observability(self, session: TaskSession) -> None:
        if session.observability.finished_at is None:
            session.observability.finish(
                wall_time=self._wall_time, monotonic=self._monotonic
            )

    def _record_call_usage(
        self, session: TaskSession, usage: TokenUsage | None
    ) -> None:
        session.observability.record_call_usage(usage)

    def _import_phase(self, session, imported, trace, snapshot_root) -> None:
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
        session.status = SessionStatus.BASELINE_TESTED
        self.say(
            f"Baseline finished: exit={result.exit_code}, "
            f"failed={result.failed_tests or 'none'}"
        )

    def _analyze_phase(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        snapshot_root: Path,
    ) -> ApprovalBinding | None:
        session.status = SessionStatus.ANALYZING
        session.consecutive_format_retries = 0
        registry = ToolRegistry(session=session, snapshot_root=snapshot_root)
        messages = [
            {"role": "user", "content": self._build_user_prompt(session, snapshot_root)}
        ]

        for step in range(self.max_analysis_steps):
            trace.emit("model_request", step=step, messages_tail=messages[-1])
            try:
                response = self.llm.complete(messages)
                self._record_call_usage(session, response.usage)
            except ModelOutputError as exc:
                # Invocation already counted; record usage outcome (may be null).
                self._record_call_usage(session, getattr(exc, "usage", None))
                if self._handle_format_error(session, trace, messages, step, exc):
                    return None
                continue
            except LLMError as exc:
                if getattr(exc, "provider_invoked", False):
                    self._record_call_usage(session, None)
                session.status = SessionStatus.ERROR
                session.stop_reason = "llm_error"
                session.last_error = str(exc)
                self.say(str(exc))
                return None

            # Schema-valid tool call → reset consecutive format retries.
            session.consecutive_format_retries = 0

            trace.emit(
                "model_response",
                step=step,
                raw_text=response.raw_text,
                tool=response.tool,
            )
            self.say(f"Agent tool: {response.tool}")
            session.observability.tool_calls_total += 1

            if response.tool == "propose_patch":
                try:
                    proposal = parse_proposal(
                        response.args, raw_text=response.raw_text
                    )
                except ModelOutputError as exc:
                    if self._handle_format_error(session, trace, messages, step, exc):
                        return None
                    continue

                session.observability.proposal_calls += 1
                validation = self.policy.validate(
                    proposal, session.workspace_root
                )
                if not validation.ok:
                    # Policy failures: feedback loop, NOT format/regeneration retry.
                    err = "Patch validation failed:\n- " + "\n- ".join(
                        validation.errors
                    )
                    trace.emit(
                        "tool_result",
                        tool="propose_patch",
                        ok=False,
                        error=err,
                        validation=validation.to_dict(),
                    )
                    messages.append(
                        {"role": "assistant", "content": response.raw_text}
                    )
                    messages.append({"role": "user", "content": err})
                    continue

                session.current_proposal = proposal
                session.status = SessionStatus.PATCH_PROPOSED
                trace.emit(
                    "patch_proposed",
                    attempt=session.attempts_used + 1,
                    proposal=proposal.to_dict(),
                    validation=validation.to_dict(),
                )

                binding = self._run_preflight(
                    session, trace, proposal, validation, messages, response.raw_text
                )
                if binding is not None:
                    return binding
                if session.status in TERMINAL_STATUSES:
                    return None
                continue

            if response.tool == "finish":
                reason = str(response.args.get("reason", "finished"))
                session.stop_reason = reason
                return None

            if response.tool in _READ_LIKE_TOOLS:
                session.observability.read_tool_calls += 1
            try:
                result_text, _terminal = execute_tool(
                    registry, response.tool, response.args
                )
                trace.emit(
                    "tool_call",
                    tool=response.tool,
                    args=response.args,
                )
                trace.emit(
                    "tool_result",
                    tool=response.tool,
                    ok=True,
                    result_preview=result_text[:4000],
                )
            except (ToolError, Exception) as exc:  # noqa: BLE001
                result_text = f"ERROR: {exc}"
                trace.emit(
                    "tool_result",
                    tool=response.tool,
                    ok=False,
                    error=str(exc),
                )

            messages.append({"role": "assistant", "content": response.raw_text})
            messages.append(
                {
                    "role": "user",
                    "content": f"TOOL_RESULT ({response.tool}):\n{result_text}",
                }
            )

        session.last_error = "Analysis step limit reached without patch"
        session.stop_reason = "analysis_step_limit"
        return None

    def _run_preflight(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        proposal: PatchProposal,
        validation: ValidationResult,
        messages: list[dict[str, str]],
        raw_assistant: str,
    ) -> ApprovalBinding | None:
        trace.emit(
            "patch_preflight_started",
            attempt=session.attempts_used + 1,
            patch_hash=patch_hash(proposal.unified_diff),
            working_tree_hash=working_tree_hash(session.workspace_root),
        )
        result = PatchPreflight.run(proposal, session.workspace_root)

        if isinstance(result, PreflightFailure) or not result.ok:
            failure = result if isinstance(result, PreflightFailure) else PreflightFailure(
                detail="preflight failed"
            )
            session.patch_preflight_failures += 1
            if session.first_patch_applicable is None:
                session.first_patch_applicable = False
            trace.emit(
                "patch_preflight_failed",
                attempt=session.attempts_used + 1,
                **failure.to_dict(),
                consecutive_patch_regeneration_retries=(
                    session.consecutive_patch_regeneration_retries
                ),
                total_patch_regeneration_retries=session.total_patch_regeneration_retries,
            )
            self.say(f"Patch preflight failed: {failure.detail}")

            if self._regeneration_exhausted(session):
                session.status = SessionStatus.PATCH_NOT_APPLICABLE
                session.stop_reason = "patch_not_applicable"
                session.last_error = failure.detail
                return None

            self._consume_regeneration(session, trace, reason=failure.detail)
            feedback = failure.feedback_message()
            messages.append({"role": "assistant", "content": raw_assistant})
            messages.append({"role": "user", "content": feedback})
            session.status = SessionStatus.ANALYZING
            return None

        session.patch_preflight_successes += 1
        if session.first_patch_applicable is None:
            session.first_patch_applicable = True
        # Do NOT reset consecutive_patch_regeneration_retries here — a later
        # apply-after-preflight mismatch must still accumulate toward NA.
        binding = ApprovalBinding(
            patch_hash=result.patch_hash,
            working_tree_hash=result.working_tree_hash,
            proposal=proposal,
            validation=validation,
        )
        trace.emit(
            "patch_preflight_succeeded",
            attempt=session.attempts_used + 1,
            patch_hash=result.patch_hash,
            working_tree_hash=result.working_tree_hash,
            files=result.files,
        )
        return binding

    def _regeneration_exhausted(self, session: TaskSession) -> bool:
        return (
            session.consecutive_patch_regeneration_retries
            >= session.max_patch_regeneration_retries
        )

    def _consume_regeneration(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        *,
        reason: str,
    ) -> None:
        session.consecutive_patch_regeneration_retries += 1
        session.total_patch_regeneration_retries += 1
        trace.emit(
            "patch_regeneration_requested",
            retry=session.consecutive_patch_regeneration_retries,
            max_patch_regeneration_retries=session.max_patch_regeneration_retries,
            total_patch_regeneration_retries=session.total_patch_regeneration_retries,
            reason=reason[:2000],
        )

    def _handle_format_error(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        messages: list[dict[str, str]],
        step: int,
        exc: ModelOutputError,
    ) -> bool:
        """Handle ModelOutputError. Return True if analysis must stop."""
        preview = raw_preview(exc.raw_text)
        trace.emit(
            "parse_failed",
            step=step,
            error_kind=exc.error_kind.value,
            error=str(exc),
            raw_preview=preview,
            consecutive_format_retries=session.consecutive_format_retries,
            total_format_retries_used=session.total_format_retries_used,
        )
        self.say(f"FORMAT_ERROR ({exc.error_kind.value}): {exc}")

        if session.consecutive_format_retries >= session.max_format_retries:
            trace.emit(
                "retry_exhausted",
                step=step,
                error_kind=exc.error_kind.value,
                error=str(exc),
                raw_preview=preview,
                consecutive_format_retries=session.consecutive_format_retries,
                total_format_retries_used=session.total_format_retries_used,
            )
            session.status = SessionStatus.MODEL_OUTPUT_INVALID
            session.stop_reason = "model_output_invalid"
            session.last_error = str(exc)
            return True

        session.consecutive_format_retries += 1
        session.total_format_retries_used += 1
        retry_no = session.consecutive_format_retries
        trace.emit(
            "format_retry",
            step=step,
            retry=retry_no,
            max_format_retries=session.max_format_retries,
            error_kind=exc.error_kind.value,
            error=str(exc),
            raw_preview=preview,
            consecutive_format_retries=session.consecutive_format_retries,
            total_format_retries_used=session.total_format_retries_used,
        )
        hint = FORMAT_RETRY_HINT.format(detail=str(exc))
        if exc.raw_text.strip():
            messages.append({"role": "assistant", "content": exc.raw_text})
        messages.append({"role": "user", "content": hint})
        return False

    def _build_user_prompt(self, session: TaskSession, snapshot_root: Path) -> str:
        parts = [
            f"Bug / request:\n{session.bug_description}",
            "",
            "Repository map:",
            session.repo_map_text,
            "",
        ]
        if session.baseline:
            b = session.baseline
            parts += [
                "Baseline pytest:",
                f"exit_code={b.exit_code}",
                f"failed_tests={b.failed_tests}",
                "traceback_summary:",
                b.traceback_summary[:3000],
                "",
            ]
        if session.last_apply_feedback:
            parts += [
                "Previous exact-apply failure after approval:",
                session.last_apply_feedback[:3000],
                "",
            ]
        if session.attempts:
            last = session.attempts[-1]
            parts.append(f"Attempts used: {session.attempts_used}/{session.max_attempts}")
            parts.append(f"Remaining attempts: {session.max_attempts - session.attempts_used}")
            if last.test_result and not last.test_result.passed:
                tr = last.test_result
                parts += [
                    "Last attempt failed tests:",
                    str(tr.failed_tests),
                    "traceback:",
                    tr.traceback_summary[:3000],
                    "stdout/stderr summary:",
                    (tr.stdout + "\n" + tr.stderr)[:2000],
                ]
            diff = current_diff_from_snapshot(snapshot_root, session.workspace_root)
            parts += [
                "",
                "Current diff vs original import:",
                diff or "(no changes)",
                "",
                f"Changed files so far: {session.changed_files}",
            ]
        parts.append(
            "Investigate with tools, then propose_patch. "
            "Incremental patches apply on the current working copy. "
            "If a patch fails preflight, read_file the target again before regenerating."
        )
        return "\n".join(parts)

    def _finalize(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        snapshot_root: Path,
    ) -> None:
        self._finish_observability(session)
        self._write_artifacts(session, snapshot_root)
        trace.emit(
            "session_finished",
            status=session.status.value,
            summary=session.to_summary(),
        )
        self.say(f"Session finished: {session.status.value}")
        self.say(f"Artifacts: {session.artifacts_dir}")

    def _docker_config_payload(self) -> dict:
        """Emit the same DockerRunConfig object used to build docker argv."""
        config = getattr(self.runner, "last_run_config", None)
        if config is None:
            return {}
        return {"docker_run_config": config.to_dict()}

    def _write_artifacts(self, session: TaskSession, snapshot_root: Path) -> None:
        artifacts = session.artifacts_dir
        artifacts.mkdir(parents=True, exist_ok=True)
        diff = current_diff_from_snapshot(snapshot_root, session.workspace_root)
        write_unified_diff(artifacts / "final.diff", diff)
        if diff:
            session.changed_files = sorted(
                set(session.changed_files) | set(changed_files_from_diff(diff))
            )
        summary = session.to_summary()
        if session.last_error and session.status in {
            SessionStatus.ERROR,
            SessionStatus.TEST_ENVIRONMENT_ERROR,
            SessionStatus.TEST_TIMEOUT,
            SessionStatus.MODEL_OUTPUT_INVALID,
            SessionStatus.PATCH_NOT_APPLICABLE,
            SessionStatus.PATCH_BASE_CHANGED,
        }:
            summary["error"] = session.last_error
        (artifacts / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )


# Backward-compatible alias used in earlier drafts.
AgentController = TaskController


def cleanup_session(session: TaskSession) -> None:
    if session.session_dir.exists():
        shutil.rmtree(session.session_dir, ignore_errors=True)
