"""Frozen, desensitized legacy model-output corpus for tool-call parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_agent.llm import FormatErrorKind, ModelOutputError, parse_tool_call
from code_agent.patching.proposal import parse_proposal
from code_agent.state import TaskSession
from code_agent.tools.registry import ToolError, ToolErrorKind, ToolRegistry, execute_tool

# Frozen historical strings (desensitized). Do not "improve" them to modern schema
# without an explicit compatibility decision.
LEGACY_ACTION_ALIAS = '{"action":"list_tree","path":"."}'
LEGACY_FLAT_ARGS = '{"tool":"search_text","query":"divide"}'
LEGACY_MARKDOWN_FENCE = (
    "```json\n"
    '{"tool":"get_repo_map","args":{}}\n'
    "```"
)
LEGACY_NO_ARGS_TOOL = '{"tool":"get_current_diff"}'
LEGACY_UNKNOWN_TOOL = '{"tool":"run_shell","args":{"cmd":"echo hi"}}'
LEGACY_PROPOSE_PATCH = (
    '{"tool":"propose_patch","args":{'
    '"diagnosis":"return wrong value",'
    '"affected_files":["mod.py"],'
    '"unified_diff":"--- a/mod.py\\n+++ b/mod.py\\n@@ -1,2 +1,2 @@\\n'
    ' def f():\\n-    return 1\\n+    return 2\\n",'
    '"expected_behavior":"f returns 2",'
    '"risk_notes":"low",'
    '"tests_to_run":["tests/test_mod.py"]'
    "}}"
)
LEGACY_TRAILING_PROSE = (
    "I will inspect the module now.\n"
    '{"tool":"read_file","args":{"path":"mod.py","start_line":"10","end_line":"25"}}\n'
    "Then propose a patch."
)
LEGACY_INVALID_JSON = '{"tool":"read_file","args":{'
LEGACY_BAD_ARG_TYPE = '{"tool":"finish","args":{"reason":["not", "a", "string"]}}'


def _registry(tmp_path: Path) -> ToolRegistry:
    root = tmp_path / "work"
    root.mkdir()
    (root / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    session = TaskSession(
        session_id="legacy",
        source_repo=root,
        session_dir=tmp_path,
        workspace_root=root,
        artifacts_dir=tmp_path / "artifacts",
        bug_description="legacy corpus",
    )
    return ToolRegistry(session=session, snapshot_root=tmp_path / "snapshot")


def test_action_alias_is_accepted() -> None:
    call = parse_tool_call(LEGACY_ACTION_ALIAS)
    assert call.tool == "list_tree"
    assert call.args["path"] == "."


def test_flat_top_level_args_without_args_object() -> None:
    call = parse_tool_call(LEGACY_FLAT_ARGS)
    assert call.tool == "search_text"
    assert call.args == {"query": "divide"}


def test_markdown_json_fence_is_stripped() -> None:
    call = parse_tool_call(LEGACY_MARKDOWN_FENCE)
    assert call.tool == "get_repo_map"
    assert call.args == {}


def test_no_args_tool_omitting_args_object() -> None:
    call = parse_tool_call(LEGACY_NO_ARGS_TOOL)
    assert call.tool == "get_current_diff"
    assert call.args == {}


def test_unknown_tool_parses_then_fails_at_execution(tmp_path: Path) -> None:
    call = parse_tool_call(LEGACY_UNKNOWN_TOOL)
    assert call.tool == "run_shell"
    with pytest.raises(ToolError) as caught:
        execute_tool(_registry(tmp_path), call.tool, call.args)
    assert caught.value.error_kind == ToolErrorKind.UNKNOWN_OR_FORBIDDEN


def test_propose_patch_historical_format_parses() -> None:
    call = parse_tool_call(LEGACY_PROPOSE_PATCH)
    assert call.tool == "propose_patch"
    proposal = parse_proposal(call.args, raw_text=LEGACY_PROPOSE_PATCH)
    assert proposal.affected_files == ["mod.py"]
    assert "return 2" in proposal.unified_diff


def test_json_extracted_after_prose_and_decimal_line_strings_coerced() -> None:
    call = parse_tool_call(LEGACY_TRAILING_PROSE)
    assert call.tool == "read_file"
    assert call.args["path"] == "mod.py"
    assert call.args["start_line"] == 10
    assert call.args["end_line"] == 25
    assert isinstance(call.args["start_line"], int)
    assert isinstance(call.args["end_line"], int)


def test_invalid_json_classified_as_decode_error() -> None:
    with pytest.raises(ModelOutputError) as caught:
        parse_tool_call(LEGACY_INVALID_JSON)
    assert caught.value.error_kind == FormatErrorKind.JSON_DECODE_ERROR


def test_illegal_argument_type_classified_as_argument_schema_error() -> None:
    with pytest.raises(ModelOutputError) as caught:
        parse_tool_call(LEGACY_BAD_ARG_TYPE)
    assert caught.value.error_kind == FormatErrorKind.INVALID_TOOL_ARGUMENT_SCHEMA


@pytest.mark.parametrize(
    "raw",
    [
        '{"tool":"read_file","args":{"path":"mod.py","start_line":true,"end_line":2}}',
        '{"tool":"read_file","args":{"path":"mod.py","start_line":1.5,"end_line":2}}',
        '{"tool":"read_file","args":{"path":"mod.py","start_line":-1,"end_line":2}}',
        '{"tool":"read_file","args":{"path":"mod.py","start_line":"","end_line":2}}',
        '{"tool":"read_file","args":{"path":"mod.py","start_line":"10x","end_line":2}}',
    ],
)
def test_non_decimal_line_values_are_rejected(raw: str) -> None:
    with pytest.raises(ModelOutputError) as caught:
        parse_tool_call(raw)
    assert caught.value.error_kind == FormatErrorKind.INVALID_TOOL_ARGUMENT_SCHEMA


def test_normalized_start_line_must_not_exceed_end_line() -> None:
    with pytest.raises(ModelOutputError) as caught:
        parse_tool_call(
            '{"tool":"read_file","args":{"path":"mod.py","start_line":"25","end_line":"10"}}'
        )
    assert caught.value.error_kind == FormatErrorKind.INVALID_TOOL_ARGUMENT_SCHEMA
    assert "start_line must be <= end_line" in str(caught.value)
