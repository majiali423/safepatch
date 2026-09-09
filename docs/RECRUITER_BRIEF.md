# SafePatch: five-minute engineering review

SafePatch repairs small Python repositories through a controlled model/tool
loop. The engineering focus is on reliable state transitions, bounded context,
reviewable patches and reproducible verification.

## What to inspect

| Question | Implementation or evidence |
| --- | --- |
| Who owns each phase? | [Architecture](ARCHITECTURE.md): controller, analysis loop, gate, executor and finalizer |
| What happens when context is stale? | [Patch recovery](PATCH_RECOVERY.md): required reads and bounded regeneration |
| Can approval become stale? | Patch and working-tree hashes are checked again before apply |
| Can tests contaminate the working copy? | Docker executes a disposable copy; [runtime tests](../tests/test_docker_e2e_negative.py) exercise the real daemon |
| How is behavior preserved during refactoring? | [Session replay](ANALYSIS_LOOP.md) and negative tests for its comparator |
| Do passing tests prove the task was solved? | [Failure case study](FAILURE_CASE_STUDY.md) compares public and hidden outcomes |

## Suggested route

1. Run the [deterministic demo](DEMO.md), which needs Docker but no API key.
2. Follow one model proposal through [AnalysisLoop](ANALYSIS_LOOP.md), approval
   and execution.
3. Inspect [three concrete cases](INTERVIEW_DEMOS.md): success, stale-context
   recovery and a hidden-test false positive.
4. Run the [development checks](DEVELOPMENT.md) and [evidence verifiers](EVALUATION.md).

The published 21-run bundle records 17/21 hidden-test successes. This is a
small historical evaluation, not a claim about current accuracy on arbitrary
repositories. See [Evaluation](EVALUATION.md) for the separate extension and
preflight experiments.

## Scope to discuss

The current automatic-repair path supports a [restricted pytest subset](PYTEST_SUPPORT.md).
Executable conftest, including normal fixtures, is excluded. The model has no
shell or direct-write tool; human approval is the default. Docker adds isolation
but is not a complete security sandbox. These choices keep the supported
workflow explicit and testable.
