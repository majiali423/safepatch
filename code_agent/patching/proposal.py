from __future__ import annotations

from typing import Any

from code_agent.llm import FormatErrorKind, ModelOutputError
from code_agent.state import PatchProposal

REQUIRED_FIELDS = (
    "diagnosis",
    "affected_files",
    "unified_diff",
    "expected_behavior",
    "risk_notes",
    "tests_to_run",
)


def parse_proposal(
    data: dict[str, Any], *, raw_text: str = ""
) -> PatchProposal:
    """Parse propose_patch args. Schema errors → ModelOutputError (format retry)."""
    if not isinstance(data, dict):
        raise ModelOutputError(
            "propose_patch args must be an object",
            error_kind=FormatErrorKind.INVALID_PROPOSAL_SCHEMA,
            raw_text=raw_text,
        )

    missing = [f for f in REQUIRED_FIELDS if f not in data or data[f] in (None, "")]
    if missing:
        raise ModelOutputError(
            f"PatchProposal missing fields: {', '.join(missing)}",
            error_kind=FormatErrorKind.INVALID_PROPOSAL_SCHEMA,
            raw_text=raw_text,
        )
    if not isinstance(data["affected_files"], list):
        raise ModelOutputError(
            "affected_files must be a list",
            error_kind=FormatErrorKind.INVALID_PROPOSAL_SCHEMA,
            raw_text=raw_text,
        )
    if not isinstance(data["tests_to_run"], list):
        raise ModelOutputError(
            "tests_to_run must be a list",
            error_kind=FormatErrorKind.INVALID_PROPOSAL_SCHEMA,
            raw_text=raw_text,
        )
    if not isinstance(data["unified_diff"], str):
        raise ModelOutputError(
            "unified_diff must be a string",
            error_kind=FormatErrorKind.INVALID_PROPOSAL_SCHEMA,
            raw_text=raw_text,
        )
    if "@@" not in data["unified_diff"]:
        if "---" not in data["unified_diff"] or "+++" not in data["unified_diff"]:
            raise ModelOutputError(
                "unified_diff must look like a unified diff",
                error_kind=FormatErrorKind.INVALID_PROPOSAL_SCHEMA,
                raw_text=raw_text,
            )
    return PatchProposal.from_dict(data)
