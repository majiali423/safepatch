from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from code_agent.patching.applier import ApplyResult, apply_proposal
from code_agent.state import ApprovalBinding, PatchProposal, SessionStatus, TestResult

ApprovalFn = Callable[[ApprovalBinding, int], bool]


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    decision: str
    stop_reason: str | None = None


class ApprovalGate:
    """Fail-closed adapter for library and CLI approval policies."""

    def __init__(self, callback: ApprovalFn | None) -> None:
        self.callback = callback

    def decide(self, binding: ApprovalBinding, attempt: int) -> ApprovalDecision:
        if self.callback is None:
            return ApprovalDecision(
                approved=False,
                decision="handler_missing",
                stop_reason="approval_handler_missing",
            )
        approved = self.callback(binding, attempt)
        return ApprovalDecision(
            approved=approved,
            decision="approve" if approved else "reject",
            stop_reason=None if approved else "user_rejected_patch",
        )


class VerificationPolicy:
    """Classify passing pytest without treating a green baseline as an oracle."""

    @staticmethod
    def passing_status(baseline: TestResult | None) -> tuple[SessionStatus, str, str]:
        if baseline is not None and baseline.passed:
            return (
                SessionStatus.TESTS_PASSED_UNVERIFIED,
                "tests_passed_unverified",
                "Tests passed, but the request is not independently verified because "
                "the baseline was already green.",
            )
        return (
            SessionStatus.SUCCEEDED,
            "all_tests_passed",
            "All tests passed; the baseline failure was resolved.",
        )


class PatchExecutionService:
    """Apply an approved proposal with the controller's fixed mutation policy."""

    def __init__(
        self,
        *,
        allow_test_changes: bool,
        allow_new_tests: bool,
        apply_fn: Callable[..., ApplyResult] = apply_proposal,
    ) -> None:
        self.allow_test_changes = allow_test_changes
        self.allow_new_tests = allow_new_tests
        self.apply_fn = apply_fn

    def apply(self, proposal: PatchProposal, workspace_root: Path) -> ApplyResult:
        return self.apply_fn(
            proposal,
            workspace_root,
            allow_test_changes=self.allow_test_changes,
            allow_new_tests=self.allow_new_tests,
        )
