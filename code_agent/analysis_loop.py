"""Model analysis / tool loop. Provider calls stay here; apply/test do not."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from code_agent.llm import (
    FORMAT_RETRY_HINT,
    LLMError,
    LLMResponse,
    ModelOutputError,
    ModelProvider,
    raw_preview,
)
from code_agent.patch_gate import PatchGate
from code_agent.patching.proposal import parse_proposal
from code_agent.patching.structured_edit import StructuredEditError, build_structured_proposal
from code_agent.patching.validator import PolicyValidator
from code_agent.repository.context_selector import format_ranked_repo_map, select_context_files
from code_agent.repository.git_diff import current_diff_from_snapshot
from code_agent.session_control import RegenerationBudget, RequiredReadTracker, SessionDeadline
from code_agent.state import (
    TERMINAL_STATUSES,
    AnalysisPhase,
    ApprovalBinding,
    SessionStatus,
    TaskSession,
    TokenUsage,
)
from code_agent.tools.registry import (
    READ_TOOLS,
    ReadBudgetExceeded,
    ToolError,
    ToolRegistry,
    execute_tool,
)
from code_agent.tools.request_evidence import (
    EvidenceErrorKind,
    EvidenceRequestError,
    execute_request_evidence,
)
from code_agent.tracing.recorder import TraceRecorder

MessageFn = Callable[[str], None]
_READ_LIKE_TOOLS = READ_TOOLS | {"get_current_diff"}


class CallUsageRecorder(Protocol):
    def record_call_usage(self, session: TaskSession, usage: TokenUsage | None) -> None: ...


class AnalysisLoop:
    """Runs the model/tool loop until a bound proposal, a terminal status, or None."""

    def __init__(
        self,
        *,
        llm: ModelProvider,
        policy: PolicyValidator,
        patch_gate: PatchGate,
        say: MessageFn,
        deadline: SessionDeadline,
        usage: CallUsageRecorder,
        required_reads: RequiredReadTracker,
        regeneration: RegenerationBudget,
        max_analysis_steps: int,
    ) -> None:
        self.llm = llm
        self.policy = policy
        self.patch_gate = patch_gate
        self.say = say
        self.deadline = deadline
        self.usage = usage
        self.required_reads = required_reads
        self.regeneration = regeneration
        self.max_analysis_steps = max_analysis_steps

    def run(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        snapshot_root: Path,
    ) -> ApprovalBinding | None:
        session.status = SessionStatus.ANALYZING
        session.consecutive_format_retries = 0
        if session.analysis_phase == AnalysisPhase.PROPOSE:
            session.analysis_phase = AnalysisPhase.SYNTHESIZE
        if (
            session.analysis_phase == AnalysisPhase.EXPLORE
            and session.exploration_read_actions_used >= session.max_read_actions
        ):
            session.analysis_phase = AnalysisPhase.SYNTHESIZE
            trace.emit(
                "synthesis_started",
                reason="exploration_budget_exhausted",
                exploration_reads=session.exploration_read_actions_used,
            )
        registry = ToolRegistry(session=session, snapshot_root=snapshot_root)
        messages = [{"role": "user", "content": self._build_user_prompt(session, snapshot_root)}]

        for step in range(self.max_analysis_steps):
            binding = self._step(session, trace, snapshot_root, registry, messages, step)
            if binding is not None:
                return binding
            if session.status in TERMINAL_STATUSES:
                return None

        session.last_error = "Analysis step limit reached without patch"
        if session.analysis_phase == AnalysisPhase.SYNTHESIZE:
            session.status = SessionStatus.READ_BUDGET_EXHAUSTED
            session.stop_reason = "synthesis_step_limit"
        else:
            session.stop_reason = "analysis_step_limit"
        return None

    def _step(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        snapshot_root: Path,
        registry: ToolRegistry,
        messages: list[dict[str, str]],
        step: int,
    ) -> ApprovalBinding | None:
        trace.emit("model_request", step=step, messages_tail=messages[-1])
        if self.deadline.abort_if_deadline(session, "before_model_request"):
            return None
        remaining = self.deadline.remaining_deadline_seconds()
        previous_timeout = getattr(self.llm, "request_timeout_seconds", None)
        if remaining is not None and isinstance(previous_timeout, (int, float)):
            setattr(
                self.llm,
                "request_timeout_seconds",
                max(0.001, min(float(previous_timeout), remaining)),
            )
        try:
            response = self.llm.complete(messages)
            self.usage.record_call_usage(session, response.usage)
        except ModelOutputError as exc:
            self.usage.record_call_usage(session, getattr(exc, "usage", None))
            self._handle_format_error(session, trace, messages, step, exc)
            return None
        except LLMError as exc:
            if getattr(exc, "provider_invoked", False):
                self.usage.record_call_usage(session, None)
            session.status = SessionStatus.ERROR
            session.stop_reason = "llm_error"
            session.last_error = str(exc)
            self.say(str(exc))
            return None
        finally:
            if isinstance(previous_timeout, (int, float)):
                setattr(self.llm, "request_timeout_seconds", previous_timeout)

        if self.deadline.abort_if_deadline(session, "after_model_response"):
            return None

        if response.tool not in {"propose_patch", "propose_edit"}:
            session.consecutive_format_retries = 0

        trace.emit(
            "model_response",
            step=step,
            raw_text=response.raw_text,
            tool=response.tool,
        )
        self.say(f"Agent tool: {response.tool}")
        session.observability.tool_calls_total += 1
        if response.tool not in READ_TOOLS:
            session.consecutive_read_budget_violations = 0

        if response.tool == "request_evidence":
            self._handle_request_evidence(session, trace, messages, response)
            return None

        if response.tool in {"propose_patch", "propose_edit"}:
            return self._handle_proposal(session, trace, messages, response, step)

        if response.tool == "finish":
            reason = str(response.args.get("reason", "finished"))
            session.analysis_phase = AnalysisPhase.FINISH
            session.status = SessionStatus.ERROR
            session.stop_reason = "agent_finished_without_patch"
            session.last_error = reason
            trace.emit("analysis_finished_without_patch", reason=reason)
            return None

        self._handle_inspection_tool(session, trace, registry, messages, response)
        return None

    def _handle_request_evidence(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        messages: list[dict[str, str]],
        response: LLMResponse,
    ) -> None:
        session.observability.read_tool_calls += 1
        try:
            result_text = execute_request_evidence(session, response.args)
        except EvidenceRequestError as exc:
            self._handle_evidence_error(
                session,
                trace,
                messages,
                response.raw_text,
                dict(response.args),
                exc,
            )
            return None
        session.consecutive_no_progress_actions = 0
        session.consecutive_read_budget_violations = 0
        trace.emit(
            "evidence_requested",
            args=response.args,
            request_number=session.evidence_requests_used,
            remaining=(session.max_evidence_requests - session.evidence_requests_used),
        )
        trace.emit(
            "tool_result",
            tool="request_evidence",
            ok=True,
            result_preview=result_text[:4000],
        )
        messages.append({"role": "assistant", "content": response.raw_text})
        messages.append(
            {
                "role": "user",
                "content": f"TOOL_RESULT (request_evidence):\n{result_text}",
            }
        )
        return None

    def _handle_proposal(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        messages: list[dict[str, str]],
        response: LLMResponse,
        step: int,
    ) -> ApprovalBinding | None:
        session.analysis_phase = AnalysisPhase.PROPOSE
        session.consecutive_no_progress_actions = 0
        if session.required_reads:
            feedback = self.required_reads.feedback(session)
            trace.emit(
                "proposal_blocked_required_read",
                required_reads=self.required_reads.payload(session),
            )
            messages.append({"role": "assistant", "content": response.raw_text})
            messages.append({"role": "user", "content": feedback})
            return None
        try:
            if response.tool == "propose_edit":
                proposal = build_structured_proposal(
                    response.args,
                    session.workspace_root,
                    raw_text=response.raw_text,
                )
            else:
                proposal = parse_proposal(
                    response.args,
                    raw_text=response.raw_text,
                )
        except ModelOutputError as exc:
            session.analysis_phase = AnalysisPhase.SYNTHESIZE
            self._handle_format_error(session, trace, messages, step, exc)
            return None
        except StructuredEditError as exc:
            session.analysis_phase = AnalysisPhase.SYNTHESIZE
            err = f"STRUCTURED_EDIT_REJECTED: {exc}"
            trace.emit(
                "tool_result",
                tool="propose_edit",
                ok=False,
                error=err,
            )
            messages.append({"role": "assistant", "content": response.raw_text})
            messages.append({"role": "user", "content": err})
            return None

        session.consecutive_format_retries = 0
        session.observability.proposal_calls += 1
        validation = self.policy.validate(proposal, session.workspace_root)
        if not validation.ok:
            session.analysis_phase = AnalysisPhase.SYNTHESIZE
            err = "Patch validation failed:\n- " + "\n- ".join(validation.errors)
            trace.emit(
                "tool_result",
                tool="propose_patch",
                ok=False,
                error=err,
                validation=validation.to_dict(),
            )
            messages.append({"role": "assistant", "content": response.raw_text})
            messages.append({"role": "user", "content": err})
            return None

        session.current_proposal = proposal
        session.status = SessionStatus.PATCH_PROPOSED
        trace.emit(
            "patch_proposed",
            attempt=session.attempts_used + 1,
            proposal=proposal.to_dict(),
            validation=validation.to_dict(),
        )
        binding = self.patch_gate.bind_if_applicable(
            session,
            trace,
            proposal,
            validation,
            messages,
            response.raw_text,
            require_failure_region_read=self.required_reads.require_failure_region_read,
            consume_regeneration=self.regeneration.consume,
            regeneration_exhausted=self.regeneration.exhausted,
        )
        if binding is not None:
            return binding
        if session.status in TERMINAL_STATUSES:
            return None
        session.analysis_phase = AnalysisPhase.SYNTHESIZE
        return None

    def _handle_inspection_tool(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        registry: ToolRegistry,
        messages: list[dict[str, str]],
        response: LLMResponse,
    ) -> None:
        if response.tool in _READ_LIKE_TOOLS:
            session.observability.read_tool_calls += 1
        result_text = ""
        try:
            was_required_recovery_read = (
                response.tool == "read_file"
                and self.required_reads.normalize_tool_path(response.args.get("path", ""))
                in session.required_reads
            )
            result_text, _terminal = execute_tool(registry, response.tool, response.args)
            session.consecutive_read_budget_violations = 0
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
            if response.tool == "read_file":
                self.required_reads.record_if_satisfied(session, trace, response.args)
            if response.tool in READ_TOOLS:
                remaining = max(
                    session.max_read_actions - session.exploration_read_actions_used,
                    0,
                )
                trace.emit(
                    "read_budget_state",
                    tool=response.tool,
                    used=session.read_actions_used,
                    exploration_used=session.exploration_read_actions_used,
                    max=session.max_read_actions,
                    remaining=remaining,
                    warning=0 < remaining <= 3,
                    exhausted=remaining == 0,
                    required_recovery_read=was_required_recovery_read,
                )
                if remaining == 0 and session.analysis_phase == AnalysisPhase.EXPLORE:
                    session.analysis_phase = AnalysisPhase.SYNTHESIZE
                    trace.emit(
                        "synthesis_started",
                        reason="exploration_budget_exhausted",
                        exploration_reads=session.exploration_read_actions_used,
                    )
        except ReadBudgetExceeded as exc:
            session.consecutive_no_progress_actions += 1
            session.total_no_progress_actions += 1
            session.consecutive_read_budget_violations = session.consecutive_no_progress_actions
            session.total_read_budget_violations += 1
            violation = session.consecutive_no_progress_actions
            result_text = (
                f"{exc}\n"
                f"consecutive_no_progress_actions={violation}/2\n"
                "General exploration is closed. Use request_evidence for a "
                "justified missing range, propose a change, or finish."
            )
            trace.emit(
                "synthesis_no_progress",
                tool=response.tool,
                args=response.args,
                used=session.read_actions_used,
                exploration_used=session.exploration_read_actions_used,
                max=session.max_read_actions,
                consecutive_violations=violation,
                total_violations=session.total_read_budget_violations,
            )
            messages.append({"role": "assistant", "content": response.raw_text})
            messages.append(
                {
                    "role": "user",
                    "content": f"TOOL_RESULT ({response.tool}):\n{result_text}",
                }
            )
            if violation >= 2:
                session.status = SessionStatus.READ_BUDGET_EXHAUSTED
                session.stop_reason = "synthesis_no_progress"
                session.last_error = (
                    "Model requested two consecutive general inspection actions "
                    "after entering SYNTHESIZE"
                )
                trace.emit(
                    "read_budget_exhausted_terminal",
                    used=session.read_actions_used,
                    exploration_used=session.exploration_read_actions_used,
                    max=session.max_read_actions,
                    reason="synthesis_no_progress",
                    total_violations=session.total_read_budget_violations,
                )
            return
        except Exception as exc:  # noqa: BLE001
            result_text = f"ERROR: {exc}"
            if response.tool in READ_TOOLS:
                session.consecutive_read_budget_violations = 0
                remaining = max(
                    session.max_read_actions - session.exploration_read_actions_used,
                    0,
                )
                trace.emit(
                    "read_budget_state",
                    tool=response.tool,
                    used=session.read_actions_used,
                    exploration_used=session.exploration_read_actions_used,
                    max=session.max_read_actions,
                    remaining=remaining,
                    warning=0 < remaining <= 3,
                    exhausted=remaining == 0,
                    tool_error=True,
                )
            trace.emit(
                "tool_result",
                tool=response.tool,
                ok=False,
                error=str(exc),
                error_kind=(
                    exc.error_kind.value
                    if isinstance(exc, ToolError)
                    else "tool_execution_error"
                ),
            )

        messages.append({"role": "assistant", "content": response.raw_text})
        messages.append(
            {
                "role": "user",
                "content": f"TOOL_RESULT ({response.tool}):\n{result_text}",
            }
        )

    def _handle_evidence_error(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        messages: list[dict[str, str]],
        raw_assistant: str,
        arguments: dict[str, object],
        error: EvidenceRequestError,
    ) -> bool:
        kind = error.kind
        promoted_parameter_error = False
        if kind == EvidenceErrorKind.PARAMETER:
            if (
                session.evidence_parameter_corrections_used
                < session.max_evidence_parameter_corrections
            ):
                session.evidence_parameter_corrections_used += 1
                feedback = (
                    f"{error}\n"
                    "CORRECTION_AVAILABLE: This parameter error did not consume an "
                    "evidence request or count as no progress. Correct it once. A "
                    "later parameter error is classified as no progress."
                )
                trace.emit(
                    "evidence_request_rejected",
                    kind=kind.value,
                    args=arguments,
                    correction_granted=True,
                    corrections_used=session.evidence_parameter_corrections_used,
                )
                messages.append({"role": "assistant", "content": raw_assistant})
                messages.append(
                    {
                        "role": "user",
                        "content": f"TOOL_RESULT (request_evidence):\n{feedback}",
                    }
                )
                return False
            kind = EvidenceErrorKind.NO_PROGRESS
            promoted_parameter_error = True

        if kind == EvidenceErrorKind.HARD_VIOLATION:
            session.hard_policy_violations += 1
            session.status = SessionStatus.READ_BUDGET_EXHAUSTED
            session.stop_reason = "evidence_hard_violation"
            session.last_error = str(error)
            trace.emit(
                "evidence_request_rejected",
                kind=kind.value,
                args=arguments,
                correction_granted=False,
            )
            trace.emit(
                "read_budget_exhausted_terminal",
                reason="evidence_hard_violation",
                evidence_requests_used=session.evidence_requests_used,
            )
            return True

        session.consecutive_no_progress_actions += 1
        session.total_no_progress_actions += 1
        session.consecutive_read_budget_violations = session.consecutive_no_progress_actions
        session.total_read_budget_violations += 1
        feedback = (
            f"{error}\n"
            f"consecutive_no_progress_actions="
            f"{session.consecutive_no_progress_actions}/2\n"
            "Use existing evidence, request a different justified range, propose a "
            "change, or finish."
        )
        trace.emit(
            "evidence_request_rejected",
            kind=kind.value,
            original_kind=error.kind.value,
            promoted_parameter_error=promoted_parameter_error,
            args=arguments,
            correction_granted=False,
            consecutive_no_progress=session.consecutive_no_progress_actions,
        )
        messages.append({"role": "assistant", "content": raw_assistant})
        messages.append(
            {
                "role": "user",
                "content": f"TOOL_RESULT (request_evidence):\n{feedback}",
            }
        )
        if session.consecutive_no_progress_actions < 2:
            return False

        session.status = SessionStatus.READ_BUDGET_EXHAUSTED
        session.stop_reason = "synthesis_no_progress"
        session.last_error = "Two consecutive no-progress actions in SYNTHESIZE"
        trace.emit(
            "read_budget_exhausted_terminal",
            reason="synthesis_no_progress",
            evidence_requests_used=session.evidence_requests_used,
        )
        return True

    def _handle_format_error(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        messages: list[dict[str, str]],
        step: int,
        exc: ModelOutputError,
    ) -> bool:
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
            f"Analysis phase: {session.analysis_phase.value}",
            (
                "Exploration reads: "
                f"{session.exploration_read_actions_used}/{session.max_read_actions}"
            ),
            (
                "Structured evidence requests: "
                f"{session.evidence_requests_used}/{session.max_evidence_requests}"
            ),
            "",
            "Repository map:",
            session.repo_map_text,
            "",
        ]
        ranked = select_context_files(
            workspace_root=session.workspace_root,
            repo_map_data=session.repo_map_data,
            traceback_summary=(
                session.baseline.traceback_summary if session.baseline else ""
            ),
            bug_description=session.bug_description,
        )
        if ranked:
            ranked_block = format_ranked_repo_map("", ranked).strip()
            insert_at = parts.index("Repository map:")
            parts[insert_at:insert_at] = [ranked_block, ""]
        retained = session.evidence.format_for_prompt()
        if retained:
            parts += [retained, ""]
        if session.baseline:
            baseline = session.baseline
            parts += [
                "Baseline pytest:",
                f"exit_code={baseline.exit_code}",
                f"failed_tests={baseline.failed_tests}",
                "traceback_summary:",
                baseline.traceback_summary[:3000],
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
                test_result = last.test_result
                parts += [
                    "Last attempt failed tests:",
                    str(test_result.failed_tests),
                    "traceback:",
                    test_result.traceback_summary[:3000],
                    "stdout/stderr summary:",
                    (test_result.stdout + "\n" + test_result.stderr)[:2000],
                ]
            diff = current_diff_from_snapshot(snapshot_root, session.workspace_root)
            parts += [
                "",
                "Current diff vs original import:",
                diff or "(no changes)",
                "",
                f"Changed files so far: {session.changed_files}",
            ]
        if session.analysis_phase == AnalysisPhase.EXPLORE:
            parts.append(
                "Investigate with tools, then propose a change. The controller enters "
                "SYNTHESIZE after 12 exploration reads. Incremental patches apply on "
                "the current working copy. If a patch fails preflight, read_file the "
                "required target range before regenerating."
            )
        else:
            parts.append(
                "SYNTHESIZE now: free exploration is closed. Map every clause in the "
                "task description to existing evidence. For state/resource changes, "
                "check creation, active use, normal cleanup, and error/close cleanup. "
                "Choose propose_edit, propose_patch, one justified request_evidence "
                "if allowance remains, or finish."
            )
        return "\n".join(parts)
