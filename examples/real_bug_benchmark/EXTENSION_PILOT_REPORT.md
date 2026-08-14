# Extension real-bug model evaluation

Evaluation date: **2026-08-13**

## Scope

This report records an extension batch separate from the earlier 21-run
historical batch. It uses the same SafePatch workflow, `deepseek-v4-flash`,
temperature `0.1`, Docker-isolated task images, public regression tests, and
post-run hidden tests. The task set, code freeze, and execution date differ
from the historical batch, so the two batches must not be used as a causal
version-to-version comparison.

All three tasks first passed environment acceptance: the buggy revision failed
both public and hidden tests, while the fixed revision passed both. The model
then received only the buggy repository, prompt, and public test overlay.
Hidden tests were injected only after the model session ended.

## Results: 9 effective runs

| Task | Project | Runs | Public | Hidden | Overall | Model calls | Tokens | Estimated cost | Duration |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `black-3` | Black | 3 | 3/3 | 3/3 | 3/3 | 34 | 383,757 | $0.011014 | 291.531 s |
| `httpie-4` | HTTPie | 3 | 3/3 | 3/3 | 3/3 | 18 | 118,076 | $0.004693 | 208.410 s |
| `tqdm-4` | tqdm | 3 | 3/3 | 3/3 | 3/3 | 27 | 218,935 | $0.007715 | 171.459 s |
| **Total** | **3 projects** | **9** | **9/9** | **9/9** | **9/9** | **79** | **720,768** | **$0.023422** | **671.400 s** |

Every effective run applied one patch successfully on its first applicable
attempt. No effective run had a preflight rejection, patch regeneration, or
apply/rollback failure. Two Black runs had one structured-output format retry
each and then completed successfully; these retries are included in the model
call and token totals.

## Availability event

One additional `tqdm-4` invocation ended in an external model-request timeout
before any exploration action or patch. The provider returned no token usage,
and no patch was applied. It is retained as an `llm_error` availability record,
but is not included among the nine effective repair runs or reported as a
repair success/failure.

## Combined audit summary

The earlier historical batch had 7 tasks and 21 effective runs: 19/21 public
test passes and 17/21 hidden/overall passes. This extension batch had 3 tasks
and 9 effective runs: 9/9 public, hidden, and overall passes.

Together, the two batches cover **10 environment-accepted historical bugs**
and **30 effective model runs**:

| Metric | Historical batch | Extension batch | Audited aggregate |
|---|---:|---:|---:|
| Effective runs | 21 | 9 | 30 |
| Public-test passes | 19 | 9 | 28/30 (93.3%) |
| Hidden / overall passes | 17 | 9 | 26/30 (86.7%) |

The aggregate is a descriptive, auditable summary across two frozen batches.
It is not a claim of a statistically controlled improvement, a current model
guarantee, or production accuracy on arbitrary repositories.

## Reproducibility boundary

Per-run raw artifacts remain locally under the gitignored
`examples/real_bug_benchmark/results/` directory because they may contain
provider usage data and large disposable workspaces. The committed task assets,
acceptance records, and this report retain the task identities, revisions,
images, test protocol, aggregate counts, and exceptional availability event.
