"""Deterministic policy, preflight, and required-read registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from code_agent.patching.hashes import patch_hash, working_tree_hash
from code_agent.patching.preflight import PatchPreflight, PreflightFailure
from code_agent.patching.validator import ValidationResult
from code_agent.state import ApprovalBinding, PatchProposal, SessionStatus, TaskSession
from code_agent.tracing.recorder import TraceRecorder


class ConsumeRegeneration(Protocol):
    def __call__(
        self, session: TaskSession, trace: TraceRecorder, *, reason: str
    ) -> None: ...


class PatchGate:
    def __init__(self, *, preflight_enabled: bool, say: Callable[[str], None]) -> None:
        self.preflight_enabled = preflight_enabled
        self.say = say

    def bind_if_applicable(
        self,
        session: TaskSession,
        trace: TraceRecorder,
        proposal: PatchProposal,
        validation: ValidationResult,
        messages: list[dict[str, str]],
        raw_assistant: str,
        *,
        require_failure_region_read: Callable[
            [TaskSession, TraceRecorder, PreflightFailure], None
        ],
        consume_regeneration: ConsumeRegeneration,
        regeneration_exhausted: Callable[[TaskSession], bool],
    ) -> ApprovalBinding | None:
        if not self.preflight_enabled:
            binding = ApprovalBinding(
                patch_hash=patch_hash(proposal.unified_diff),
                working_tree_hash=working_tree_hash(session.workspace_root),
                proposal=proposal,
                validation=validation,
            )
            trace.emit(
                "patch_preflight_bypassed",
                attempt=session.attempts_used + 1,
                patch_hash=binding.patch_hash,
                working_tree_hash=binding.working_tree_hash,
            )
            return binding

        trace.emit(
            "patch_preflight_started",
            attempt=session.attempts_used + 1,
            patch_hash=patch_hash(proposal.unified_diff),
            working_tree_hash=working_tree_hash(session.workspace_root),
        )
        result = PatchPreflight.run(proposal, session.workspace_root)
        if isinstance(result, PreflightFailure) or not result.ok:
            failure = (
                result
                if isinstance(result, PreflightFailure)
                else PreflightFailure(detail="preflight failed")
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
            if regeneration_exhausted(session):
                session.status = SessionStatus.PATCH_NOT_APPLICABLE
                session.stop_reason = "patch_not_applicable"
                session.last_error = failure.detail
                return None
            require_failure_region_read(session, trace, failure)
            consume_regeneration(session, trace, reason=failure.detail)
            messages.append({"role": "assistant", "content": raw_assistant})
            messages.append({"role": "user", "content": failure.feedback_message()})
            session.status = SessionStatus.ANALYZING
            return None

        session.patch_preflight_successes += 1
        if session.first_patch_applicable is None:
            session.first_patch_applicable = True
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
            relocations=result.relocations,
        )
        return binding
