# Failure case study: passing public tests is not enough

SafePatch's frozen 21-run real-bug benchmark produced two especially useful
false positives. In both cases the model proposed an applicable patch and the
public regression test passed, but a hidden semantic test rejected the repair.
These failures show why patch safety and semantic correctness must be measured
separately.

## How the hidden tests work

The model sees the buggy repository, task description, and public tests. It
does not see the reference fix or hidden tests. Only after the agent finishes,
the evaluator copies the completed working tree, injects the hidden test files,
and runs pytest again. Hidden-test output is never returned to the repair loop.

This arrangement does not make hidden tests a perfect oracle. It does prevent
the model from editing toward their exact assertions and checks behavior that
the public regression test omitted.

## Tornado: lifecycle state was discarded too early

The bug involved a `RequestHandler` retained by a live WebSocket connection.
Tornado's normal HTTP cleanup cleared handler state before later WebSocket
callbacks used it. The submitted patch merely skipped a namespace update when
`self.ui` was already `None`.

That patch removed the immediate exception, so the public test passed. It did
not restore the required lifecycle invariant:

- while the WebSocket is alive, template/UI state must remain available;
- after the WebSocket closes, that state should be released.

The hidden test inspected both moments. It found `handler.ui is None` during an
active connection and correctly rejected the patch. A sound fix needs to change
the save/restore and close-time cleanup flow across the relevant files, not
merely guard the line that crashes.

Evidence: [submitted diff](../examples/real_bug_benchmark/published/synthesis-21-run/failures/tornado-10-hidden-lifecycle.diff)
and [hidden-test output](../examples/real_bug_benchmark/published/synthesis-21-run/failures/tornado-10-hidden-lifecycle.txt).

## tqdm: explicit `total` must take precedence

The task defined the truth value of a progress bar from its known total. The
submitted `__bool__` implementation checked the iterable length before the
explicit `total`. That happens to satisfy ordinary cases such as `tqdm([])` and
`tqdm([1])`, so the public test passed.

It fails when the two sources disagree. For `tqdm([], total=2)`, the explicit
total is authoritative and the object should be true. The submitted patch used
the empty iterable first and returned false. The hidden boundary test exposed
this precedence error.

Evidence: [submitted diff](../examples/real_bug_benchmark/published/synthesis-21-run/failures/tqdm-3-hidden-total-precedence.diff)
and [hidden-test output](../examples/real_bug_benchmark/published/synthesis-21-run/failures/tqdm-3-hidden-total-precedence.txt).

## What these failures mean

They are model reasoning failures, not patch-application failures. Preflight
proved that the proposed text matched the repository and could be applied
exactly; it could not prove that the behavior was correct. Docker then executed
the tests faithfully, and the hidden layer identified the missing semantics.

The engineering conclusion is deliberately limited: SafePatch can prevent many
unsafe or stale patches from reaching execution, but correctness still depends
on test quality and the model's understanding of program behavior. The project
therefore reports public and hidden outcomes separately and retains failed
patches as auditable evidence.
