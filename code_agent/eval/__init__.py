"""Evaluator-only helpers (hidden tests). Not part of the product Agent loop."""

from code_agent.eval.hidden import (
    discover_hidden_dir,
    evaluate_after_product,
    run_hidden_tests,
)
from code_agent.eval.status import EvalStatus

__all__ = [
    "EvalStatus",
    "discover_hidden_dir",
    "evaluate_after_product",
    "run_hidden_tests",
]
