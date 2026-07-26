# sample02_normalize — evaluation note

`sample02_normalize` ships with a **fixed dry-run hard-coded patch**
(`examples/eval_tasks/dry_run_sample02_hardcode.json`) that only handles the
public case `" Alice "` → `"alice"`.

This is intentional infrastructure proof for hidden tests:

- product `summary.status` can be `SUCCEEDED` (public tests pass);
- eval `eval_status` becomes `HIDDEN_TESTS_FAILED` (hidden suite catches the cheat).

**It does not represent real LLM capability or quality.** Live-model runs are a
separate evaluation concern.
