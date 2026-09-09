# Historical v1 session replay (limited evidence)

Copied from `output/analysis_loop_split_0908/runs` for version-controlled tests.
Do not treat this tree as proof that pre- and post-AnalysisLoop-split trace
payloads were identical.

What it actually keeps:

- scenario `fingerprint.json` counters, status, stop_reason, event **names**,
  and `final.diff` / `workspace_mod` where present
- `summary.json` for most scenarios (already old-normalized timestamps)
- `trace.events.json` event-name lists only

What it does not keep:

- full `trace.jsonl` payloads (approval hashes, tool args, messages)
- reliable patch/worktree hashes inside `last_error` (old global hex rewrite)

`cancelled` has no `summary.json` / `final.diff` in this snapshot.

Compare mode against a v2 capture therefore checks only retained fields.
For complete summary/trace/diff regression use `current_v2`.
