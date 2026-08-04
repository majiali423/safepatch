from __future__ import annotations

from pathlib import Path

import pytest

from code_agent.llm import (
    FormatErrorKind,
    LLMClient,
    LLMError,
    ModelOutputError,
    ToolCall,
    parse_tool_call,
)
from code_agent.patching.proposal import parse_proposal
from code_agent.state import TaskSession
from code_agent.tools.registry import (
    ToolError,
    ToolErrorKind,
    ToolRegistry,
    execute_tool,
)


def _registry(tmp_path: Path) -> ToolRegistry:
    root = tmp_path / "work"
    root.mkdir()
    session = TaskSession(
        session_id="test",
        source_repo=root,
        session_dir=tmp_path,
        workspace_root=root,
        artifacts_dir=tmp_path / "artifacts",
        bug_description="test",
    )
    return ToolRegistry(session=session, snapshot_root=tmp_path / "snapshot")


def test_valid_tool_call_uses_unified_type() -> None:
    call = parse_tool_call('{"tool":"get_repo_map","args":{}}')
    assert isinstance(call, ToolCall)
    assert call.tool == "get_repo_map"
    assert call.args == {}


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("not json", FormatErrorKind.JSON_DECODE_ERROR),
        ('{"args":{}}', FormatErrorKind.INVALID_TOOL_SCHEMA),
        (
            '{"tool":"read_file","args":{"start_line":"one"}}',
            FormatErrorKind.INVALID_TOOL_ARGUMENT_SCHEMA,
        ),
    ],
)
def test_decode_top_level_and_argument_errors_are_distinct(raw: str, kind: FormatErrorKind):
    with pytest.raises(ModelOutputError) as caught:
        parse_tool_call(raw)
    assert caught.value.error_kind == kind


def test_proposal_schema_error_is_distinct() -> None:
    with pytest.raises(ModelOutputError) as caught:
        parse_proposal({"diagnosis": "missing everything else"})
    assert caught.value.error_kind == FormatErrorKind.INVALID_PROPOSAL_SCHEMA


def test_unknown_tool_is_not_a_format_error(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as caught:
        execute_tool(_registry(tmp_path), "shell", {})
    assert caught.value.error_kind == ToolErrorKind.UNKNOWN_OR_FORBIDDEN


def test_tool_execution_error_is_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("read failed")

    monkeypatch.setattr("code_agent.tools.registry.list_tree", fail)
    with pytest.raises(ToolError) as caught:
        execute_tool(_registry(tmp_path), "list_tree", {"path": "."})
    assert caught.value.error_kind == ToolErrorKind.EXECUTION_ERROR


def test_provider_configuration_error_is_not_model_format_error() -> None:
    client = LLMClient(api_key="", dry_run_script=[])
    client.api_key = None
    with pytest.raises(LLMError) as caught:
        client.complete([{"role": "user", "content": "test"}])
    assert caught.value.provider_invoked is False
