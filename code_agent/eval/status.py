"""Eval-layer status — separate from product SessionStatus."""

from __future__ import annotations

from enum import Enum


class EvalStatus(str, Enum):
    PUBLIC_TESTS_FAILED = "PUBLIC_TESTS_FAILED"
    HIDDEN_TESTS_FAILED = "HIDDEN_TESTS_FAILED"
    SUCCEEDED = "SUCCEEDED"
    HIDDEN_TEST_ENVIRONMENT_ERROR = "HIDDEN_TEST_ENVIRONMENT_ERROR"
    HIDDEN_TEST_TIMEOUT = "HIDDEN_TEST_TIMEOUT"
