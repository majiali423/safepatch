from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


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
    ERROR = "ERROR"


TERMINAL_STATUSES = {
    SessionStatus.SUCCEEDED,
    SessionStatus.FAILED_MAX_ATTEMPTS,
    SessionStatus.PATCH_NOT_APPLICABLE,
    SessionStatus.PATCH_BASE_CHANGED,
    SessionStatus.REJECTED,
    SessionStatus.TEST_ENVIRONMENT_ERROR,
    SessionStatus.TEST_TIMEOUT,
    SessionStatus.MODEL_OUTPUT_INVALID,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis": self.diagnosis,
            "affected_files": self.affected_files,
            "unified_diff": self.unified_diff,
            "expected_behavior": self.expected_behavior,
            "risk_notes": self.risk_notes,
            "tests_to_run": self.tests_to_run,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatchProposal":
        return cls(
            diagnosis=str(data.get("diagnosis", "")),
            affected_files=list(data.get("affected_files") or []),
            unified_diff=str(data.get("unified_diff", "")),
            expected_behavior=str(data.get("expected_behavior", "")),
            risk_notes=str(data.get("risk_notes", "")),
            tests_to_run=list(data.get("tests_to_run") or []),
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
        validation_dict = (
            validation.to_dict() if hasattr(validation, "to_dict") else {}
        )
        return {
            "patch_hash": self.patch_hash,
            "working_tree_hash": self.working_tree_hash,
            "proposal": self.proposal.to_dict(),
            "validation": validation_dict,
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
    max_read_actions: int = 12
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
    first_patch_applicable: bool | None = None
    last_apply_feedback: str = ""

    def to_summary(self) -> dict[str, Any]:
        baseline_passed = bool(self.baseline and self.baseline.passed)
        final_passed = self.status == SessionStatus.SUCCEEDED
        preflight_total = self.patch_preflight_successes + self.patch_preflight_failures
        preflight_rate = (
            self.patch_preflight_successes / preflight_total if preflight_total else None
        )
        return {
            "status": self.status.value,
            "attempts_used": self.attempts_used,
            "changed_files": sorted(set(self.changed_files)),
            "baseline_tests_passed": baseline_passed,
            "final_tests_passed": final_passed,
            "stop_reason": self.stop_reason
            or _default_stop_reason(self.status),
            "consecutive_format_retries": self.consecutive_format_retries,
            "total_format_retries_used": self.total_format_retries_used,
            "consecutive_patch_regeneration_retries": (
                self.consecutive_patch_regeneration_retries
            ),
            "total_patch_regeneration_retries": self.total_patch_regeneration_retries,
            "patch_preflight_failures": self.patch_preflight_failures,
            "patch_preflight_successes": self.patch_preflight_successes,
            "patch_preflight_success_rate": preflight_rate,
            "patch_apply_failures_after_preflight": (
                self.patch_apply_failures_after_preflight
            ),
            "first_patch_applicable": self.first_patch_applicable,
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
        SessionStatus.ERROR: "internal_error",
    }
    return mapping.get(status, status.value.lower())
