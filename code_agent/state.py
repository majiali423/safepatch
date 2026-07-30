from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

# First versioned summary schema that includes the observability card.
SUMMARY_SCHEMA_VERSION = 1


class SessionStatus(str, Enum):
    CREATED = "CREATED"
    IMPORTED = "IMPORTED"
    BASELINE_TESTED = "BASELINE_TESTED"
    ANALYZING = "ANALYZING"
    PATCH_PROPOSED = "PATCH_PROPOSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PATCH_APPLIED = "PATCH_APPLIED"
    TESTING = "TESTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_MAX_ATTEMPTS = "FAILED_MAX_ATTEMPTS"
    PATCH_NOT_APPLICABLE = "PATCH_NOT_APPLICABLE"
    PATCH_BASE_CHANGED = "PATCH_BASE_CHANGED"
    REJECTED = "REJECTED"
    TEST_ENVIRONMENT_ERROR = "TEST_ENVIRONMENT_ERROR"
    TEST_TIMEOUT = "TEST_TIMEOUT"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    READ_BUDGET_EXHAUSTED = "READ_BUDGET_EXHAUSTED"
    ERROR = "ERROR"


class AnalysisPhase(str, Enum):
    EXPLORE = "EXPLORE"
    SYNTHESIZE = "SYNTHESIZE"
    PROPOSE = "PROPOSE"
    FINISH = "FINISH"


TERMINAL_STATUSES = {
    SessionStatus.SUCCEEDED,
    SessionStatus.FAILED_MAX_ATTEMPTS,
    SessionStatus.PATCH_NOT_APPLICABLE,
    SessionStatus.PATCH_BASE_CHANGED,
    SessionStatus.REJECTED,
    SessionStatus.TEST_ENVIRONMENT_ERROR,
    SessionStatus.TEST_TIMEOUT,
    SessionStatus.MODEL_OUTPUT_INVALID,
    SessionStatus.READ_BUDGET_EXHAUSTED,
    SessionStatus.ERROR,
}


@dataclass
class TestResult:
    __test__ = False  # not a pytest test class

    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    failed_tests: list[str] = field(default_factory=list)
    traceback_summary: str = ""
    environment_error: str | None = None
    # Distinguishes assertion/test failures (None) from infra problems.
    # "environment" = docker/pytest missing; "timeout" = wall-clock limit.
    error_kind: str | None = None

    @property
    def passed(self) -> bool:
        return self.environment_error is None and self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_sec": self.duration_sec,
            "failed_tests": self.failed_tests,
            "traceback_summary": self.traceback_summary,
            "environment_error": self.environment_error,
            "error_kind": self.error_kind,
            "passed": self.passed,
        }


@dataclass
class PatchProposal:
    diagnosis: str
    affected_files: list[str]
    unified_diff: str
    expected_behavior: str
    risk_notes: str
    tests_to_run: list[str]
    base_revisions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis": self.diagnosis,
            "affected_files": self.affected_files,
            "unified_diff": self.unified_diff,
            "expected_behavior": self.expected_behavior,
            "risk_notes": self.risk_notes,
            "tests_to_run": self.tests_to_run,
            "base_revisions": self.base_revisions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatchProposal:
        return cls(
            diagnosis=str(data.get("diagnosis", "")),
            affected_files=list(data.get("affected_files") or []),
            unified_diff=str(data.get("unified_diff", "")),
            expected_behavior=str(data.get("expected_behavior", "")),
            risk_notes=str(data.get("risk_notes", "")),
            tests_to_run=list(data.get("tests_to_run") or []),
            base_revisions={
                str(path): str(revision)
                for path, revision in (data.get("base_revisions") or {}).items()
            },
        )


@dataclass
class ApprovalBinding:
    """Approval is bound to exact patch + working-tree content hashes."""

    patch_hash: str
    working_tree_hash: str
    proposal: PatchProposal
    validation: Any  # ValidationResult; typed loosely to avoid import cycles

    def to_dict(self) -> dict[str, Any]:
        validation = self.validation
        validation_dict = validation.to_dict() if hasattr(validation, "to_dict") else {}
        return {
            "patch_hash": self.patch_hash,
            "working_tree_hash": self.working_tree_hash,
            "proposal": self.proposal.to_dict(),
            "validation": validation_dict,
        }


@dataclass
class TokenUsage:
    """Provider token usage. None means the provider did not report that field."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    source: str = "unavailable"

    @property
    def available(self) -> bool:
        return any(
            v is not None
            for v in (
                self.prompt_tokens,
                self.completion_tokens,
                self.total_tokens,
                self.cached_tokens,
            )
        )

    def add(self, other: TokenUsage | None) -> TokenUsage:
        """Accumulate another usage record. Missing fields stay None until seen.

        Does not estimate missing values. Source metadata is refreshed by
        SessionObservability after each call.
        """
        if other is None or not other.available:
            return self
        if not self.available:
            return TokenUsage(
                prompt_tokens=other.prompt_tokens,
                completion_tokens=other.completion_tokens,
                total_tokens=other.total_tokens,
                cached_tokens=other.cached_tokens,
                source=other.source,
            )

        def _sum(a: int | None, b: int | None) -> int | None:
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        return TokenUsage(
            prompt_tokens=_sum(self.prompt_tokens, other.prompt_tokens),
            completion_tokens=_sum(self.completion_tokens, other.completion_tokens),
            total_tokens=_sum(self.total_tokens, other.total_tokens),
            cached_tokens=_sum(self.cached_tokens, other.cached_tokens),
            source="provider",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "source": self.source,
            "available": self.source != "unavailable" and self.available,
        }

    @classmethod
    def unavailable(cls) -> TokenUsage:
        return cls(source="unavailable")


@dataclass
class SessionObservability:
    """Per-session machine-readable cost/latency counters (local only)."""

    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    _mono_start: float | None = field(default=None, repr=False)
    model_calls: int = 0
    calls_with_usage: int = 0
    calls_without_usage: int = 0
    tool_calls_total: int = 0
    read_tool_calls: int = 0
    proposal_calls: int = 0
    baseline_pytest_runs: int = 0
    post_apply_pytest_runs: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage.unavailable)
    model_provider: str = "unknown"
    model_name: str = ""
    tool_calling_protocol: str = "custom_json"
    temperature: float | None = None

    def start(
        self,
        *,
        wall_time: Callable[[], float],
        monotonic: Callable[[], float],
    ) -> None:
        self._mono_start = monotonic()
        self.started_at = datetime.fromtimestamp(wall_time(), tz=timezone.utc).isoformat()
        self.finished_at = None
        self.duration_ms = None

    def finish(
        self,
        *,
        wall_time: Callable[[], float],
        monotonic: Callable[[], float],
    ) -> None:
        self.finished_at = datetime.fromtimestamp(wall_time(), tz=timezone.utc).isoformat()
        if self._mono_start is not None:
            elapsed = max(0.0, monotonic() - self._mono_start)
            self.duration_ms = round(elapsed * 1000.0)
        elif self.duration_ms is None:
            self.duration_ms = 0

    def note_provider_invocation(self) -> None:
        """Count a provider invocation attempt (before the call returns)."""
        self.model_calls += 1

    def record_call_usage(self, usage: TokenUsage | None = None) -> None:
        """Record usage outcome for one already-counted provider invocation."""
        if usage is not None and usage.available:
            self.calls_with_usage += 1
            self.token_usage = self.token_usage.add(usage)
        else:
            self.calls_without_usage += 1
        self._refresh_usage_meta()

    def _refresh_usage_meta(self) -> None:
        if self.calls_with_usage == 0:
            self.token_usage.source = "unavailable"
        elif self.calls_without_usage > 0:
            self.token_usage.source = "provider_partial"
        else:
            self.token_usage.source = "provider"

    @property
    def usage_complete(self) -> bool:
        return (
            self.model_calls > 0
            and self.calls_with_usage == self.model_calls
            and self.calls_without_usage == 0
        )

    def usage_to_dict(self) -> dict[str, Any]:
        data = self.token_usage.to_dict()
        data["source"] = self.token_usage.source
        data["available"] = self.token_usage.source != "unavailable"
        data["complete"] = self.usage_complete
        data["calls_with_usage"] = self.calls_with_usage
        data["calls_without_usage"] = self.calls_without_usage
        return data

    def to_dict(
        self,
        *,
        format_retries: int,
        patch_regeneration_retries: int,
        repair_attempts: int,
        max_format_retries: int,
        max_patch_regeneration_retries: int,
        max_repair_attempts: int,
    ) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms if self.duration_ms is not None else 0,
            "model": {
                "provider": self.model_provider,
                "name": self.model_name,
                "tool_calling_protocol": self.tool_calling_protocol,
                "temperature": self.temperature,
                "max_format_retries": max_format_retries,
                "max_patch_regeneration_retries": max_patch_regeneration_retries,
                "max_repair_attempts": max_repair_attempts,
                "calls": self.model_calls,
                "usage": self.usage_to_dict(),
            },
            "tools": {
                "total_calls": self.tool_calls_total,
                "read_calls": self.read_tool_calls,
                "proposal_calls": self.proposal_calls,
            },
            "retries": {
                "format": format_retries,
                "patch_regeneration": patch_regeneration_retries,
                "repair_attempts": repair_attempts,
            },
            "tests": {
                "total_runs": self.baseline_pytest_runs + self.post_apply_pytest_runs,
                "baseline_runs": self.baseline_pytest_runs,
                "post_apply_runs": self.post_apply_pytest_runs,
            },
        }


@dataclass
class AttemptRecord:
    attempt: int
    proposal: PatchProposal | None = None
    approved: bool | None = None
    apply_ok: bool | None = None
    apply_error: str | None = None
    test_result: TestResult | None = None
    patch_hash: str | None = None
    working_tree_hash: str | None = None


@dataclass
class TaskSession:
    session_id: str
    source_repo: Path
    session_dir: Path
    workspace_root: Path
    artifacts_dir: Path
    bug_description: str
    status: SessionStatus = SessionStatus.CREATED
    max_attempts: int = 3
    attempts_used: int = 0
    read_actions_used: int = 0
    exploration_read_actions_used: int = 0
    max_read_actions: int = 12
    analysis_phase: AnalysisPhase = AnalysisPhase.EXPLORE
    evidence_requests_used: int = 0
    max_evidence_requests: int = 2
    evidence_parameter_corrections_used: int = 0
    max_evidence_parameter_corrections: int = 1
    consecutive_no_progress_actions: int = 0
    total_no_progress_actions: int = 0
    hard_policy_violations: int = 0
    required_recovery_reads_used: int = 0
    successful_read_ranges: dict[str, list[tuple[int, int]]] = field(
        default_factory=dict,
        repr=False,
    )
    repo_map_text: str = ""
    repo_map_data: dict[str, Any] = field(default_factory=dict)
    baseline: TestResult | None = None
    current_proposal: PatchProposal | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    stop_reason: str = ""
    last_error: str = ""
    # Format-retry bookkeeping (does NOT count toward attempts_used).
    max_format_retries: int = 2
    consecutive_format_retries: int = 0
    total_format_retries_used: int = 0
    # Patch regeneration (preflight mismatch); does NOT count toward attempts_used.
    max_patch_regeneration_retries: int = 2
    consecutive_patch_regeneration_retries: int = 0
    total_patch_regeneration_retries: int = 0
    patch_preflight_failures: int = 0
    patch_preflight_successes: int = 0
    patch_apply_failures_after_preflight: int = 0
    patch_apply_failures_without_preflight: int = 0
    first_patch_applicable: bool | None = None
    last_apply_feedback: str = ""
    # A context-stale file must be re-read over this inclusive range before
    # another proposal is accepted for preflight.
    required_reads: dict[str, tuple[int, int]] = field(default_factory=dict)
    consecutive_read_budget_violations: int = 0
    total_read_budget_violations: int = 0
    observability: SessionObservability = field(default_factory=SessionObservability)

    def to_summary(self) -> dict[str, Any]:
        baseline_passed = bool(self.baseline and self.baseline.passed)
        final_passed = self.status == SessionStatus.SUCCEEDED
        preflight_total = self.patch_preflight_successes + self.patch_preflight_failures
        preflight_rate = (
            self.patch_preflight_successes / preflight_total if preflight_total else None
        )
        return {
            "summary_schema_version": SUMMARY_SCHEMA_VERSION,
            "status": self.status.value,
            "attempts_used": self.attempts_used,
            "changed_files": sorted(set(self.changed_files)),
            "baseline_tests_passed": baseline_passed,
            "final_tests_passed": final_passed,
            "stop_reason": self.stop_reason or _default_stop_reason(self.status),
            "consecutive_format_retries": self.consecutive_format_retries,
            "total_format_retries_used": self.total_format_retries_used,
            "consecutive_patch_regeneration_retries": (self.consecutive_patch_regeneration_retries),
            "total_patch_regeneration_retries": self.total_patch_regeneration_retries,
            "patch_preflight_failures": self.patch_preflight_failures,
            "patch_preflight_successes": self.patch_preflight_successes,
            "patch_preflight_success_rate": preflight_rate,
            "patch_apply_failures_after_preflight": (self.patch_apply_failures_after_preflight),
            "patch_apply_failures_without_preflight": (self.patch_apply_failures_without_preflight),
            "first_patch_applicable": self.first_patch_applicable,
            "analysis": {
                "phase": self.analysis_phase.value,
                "evidence_requests_used": self.evidence_requests_used,
                "max_evidence_requests": self.max_evidence_requests,
                "parameter_corrections_used": (
                    self.evidence_parameter_corrections_used
                ),
                "max_parameter_corrections": (
                    self.max_evidence_parameter_corrections
                ),
                "consecutive_no_progress_actions": (
                    self.consecutive_no_progress_actions
                ),
                "total_no_progress_actions": self.total_no_progress_actions,
                "hard_policy_violations": self.hard_policy_violations,
            },
            "read_budget": {
                "used": self.exploration_read_actions_used,
                "max": self.max_read_actions,
                "remaining": max(
                    self.max_read_actions - self.exploration_read_actions_used,
                    0,
                ),
                "total_read_actions": self.read_actions_used,
                "required_recovery_reads": self.required_recovery_reads_used,
                "consecutive_violations": self.consecutive_read_budget_violations,
                "total_violations": self.total_read_budget_violations,
            },
            "observability": self.observability.to_dict(
                format_retries=self.total_format_retries_used,
                patch_regeneration_retries=self.total_patch_regeneration_retries,
                repair_attempts=self.attempts_used,
                max_format_retries=self.max_format_retries,
                max_patch_regeneration_retries=self.max_patch_regeneration_retries,
                max_repair_attempts=self.max_attempts,
            ),
        }


def _default_stop_reason(status: SessionStatus) -> str:
    mapping = {
        SessionStatus.SUCCEEDED: "all_tests_passed",
        SessionStatus.FAILED_MAX_ATTEMPTS: "max_patch_attempts_reached",
        SessionStatus.PATCH_NOT_APPLICABLE: "patch_not_applicable",
        SessionStatus.PATCH_BASE_CHANGED: "patch_base_changed",
        SessionStatus.REJECTED: "user_rejected_patch",
        SessionStatus.TEST_ENVIRONMENT_ERROR: "test_environment_error",
        SessionStatus.TEST_TIMEOUT: "pytest_timeout",
        SessionStatus.MODEL_OUTPUT_INVALID: "model_output_invalid",
        SessionStatus.READ_BUDGET_EXHAUSTED: "read_budget_exhausted",
        SessionStatus.ERROR: "internal_error",
    }
    return mapping.get(status, status.value.lower())
