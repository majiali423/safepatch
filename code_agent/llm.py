from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from code_agent.tracing.recorder import _redact

SYSTEM_PROMPT = """You are a careful code-repair agent for a small local Python repository.
You may only use these tools via a single JSON object per turn:

list_tree(path)
read_file(path, start_line, end_line)
search_text(query)
search_symbol(symbol)
get_repo_map()
get_current_diff()
propose_patch(diagnosis, affected_files, unified_diff, expected_behavior, risk_notes, tests_to_run)
finish(reason)

Rules:
- Never request shell, docker, network, or direct file writes.
- Prefer repo map + targeted reads. Max 12 read-like tool calls.
- read_file allows at most 120 lines per call.
- When ready to change code, call propose_patch with a valid unified diff.
- Only modify existing .py files, or add new .py files under tests/.
- Do not delete/rename files. Do not touch deps, Docker, CI, .git, or .env.
- Max 5 files and 300 changed lines per patch.
- Output ONLY one JSON object, no markdown fences, no extra text.

JSON formats:
{"tool":"read_file","args":{"path":"src/x.py","start_line":1,"end_line":80}}
{"tool":"propose_patch","args":{"diagnosis":"...","affected_files":["..."],"unified_diff":"...","expected_behavior":"...","risk_notes":"...","tests_to_run":["..."]}}
{"tool":"finish","args":{"reason":"..."}}
"""

RAW_PREVIEW_MAX = 2000

FORMAT_RETRY_HINT = (
    "FORMAT_ERROR: Your previous reply was not a valid single JSON tool call. "
    "Return ONLY one JSON object with keys tool and args. "
    "No markdown fences, no commentary. "
    "Error detail: {detail}"
)


class FormatErrorKind(str, Enum):
    JSON_DECODE_ERROR = "json_decode_error"
    INVALID_TOOL_SCHEMA = "invalid_tool_schema"
    INVALID_PROPOSAL_SCHEMA = "invalid_proposal_schema"


@dataclass
class LLMResponse:
    raw_text: str
    tool: str
    args: dict[str, Any]


class LLMError(RuntimeError):
    """API / configuration failures (not model format issues)."""


class ModelOutputError(Exception):
    """Model returned unusable structured output (eligible for format retry)."""

    def __init__(
        self,
        message: str,
        *,
        error_kind: FormatErrorKind,
        raw_text: str = "",
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.raw_text = raw_text or ""


class LLMClient:
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        dry_run_script: list[Any] | None = None,
    ) -> None:
        self.model = model or os.getenv("CODE_AGENT_MODEL", "gpt-4o-mini")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv(
            "CODE_AGENT_API_KEY"
        )
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or os.getenv(
            "CODE_AGENT_BASE_URL"
        )
        self._dry_run_script = list(dry_run_script or [])
        self._dry_idx = 0
        self._client = None

    def complete(self, messages: list[dict[str, str]]) -> LLMResponse:
        raw = self._fetch_raw(messages)
        parsed = parse_tool_call(raw)
        return LLMResponse(
            raw_text=raw, tool=parsed["tool"], args=parsed.get("args", {})
        )

    def _fetch_raw(self, messages: list[dict[str, str]]) -> str:
        if self._dry_run_script:
            if self._dry_idx >= len(self._dry_run_script):
                raise LLMError("Dry-run script exhausted")
            item = self._dry_run_script[self._dry_idx]
            self._dry_idx += 1
            if isinstance(item, str):
                return item
            if isinstance(item, dict) and "raw" in item:
                return str(item["raw"])
            return json.dumps(item, ensure_ascii=False)

        if not self.api_key:
            raise LLMError(
                "No API key. Set OPENAI_API_KEY or use --dry-run with a script."
            )

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            )
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LLMError(str(exc)) from exc

        return response.choices[0].message.content or ""

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client


def raw_preview(text: str, *, max_len: int = RAW_PREVIEW_MAX) -> str:
    """Redact secrets and truncate for trace storage."""
    redacted = _redact(text if isinstance(text, str) else str(text))
    if not isinstance(redacted, str):
        redacted = str(redacted)
    if len(redacted) <= max_len:
        return redacted
    return redacted[:max_len] + f"...[truncated,{len(redacted)} chars]"


def parse_tool_call(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ModelOutputError(
            "Model did not return a JSON object",
            error_kind=FormatErrorKind.JSON_DECODE_ERROR,
            raw_text=text,
        )
    blob = cleaned[start : end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ModelOutputError(
            f"Invalid JSON from model: {exc}",
            error_kind=FormatErrorKind.JSON_DECODE_ERROR,
            raw_text=text,
        ) from exc

    if not isinstance(data, dict):
        raise ModelOutputError(
            "JSON root must be an object",
            error_kind=FormatErrorKind.INVALID_TOOL_SCHEMA,
            raw_text=text,
        )

    if "tool" not in data:
        if "action" in data:
            data = {
                "tool": data["action"],
                "args": data.get("args")
                or {k: v for k, v in data.items() if k not in {"action", "args"}},
            }
        else:
            raise ModelOutputError(
                "JSON missing 'tool' field",
                error_kind=FormatErrorKind.INVALID_TOOL_SCHEMA,
                raw_text=text,
            )

    args = data.get("args")
    if args is None:
        args = {k: v for k, v in data.items() if k != "tool"}
    if not isinstance(args, dict):
        raise ModelOutputError(
            "'args' must be an object",
            error_kind=FormatErrorKind.INVALID_TOOL_SCHEMA,
            raw_text=text,
        )
    tool = str(data["tool"]).strip()
    if not tool:
        raise ModelOutputError(
            "'tool' must be a non-empty string",
            error_kind=FormatErrorKind.INVALID_TOOL_SCHEMA,
            raw_text=text,
        )
    return {"tool": tool, "args": args}
