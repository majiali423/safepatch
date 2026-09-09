# Current-version v2 baseline

Label: `current_working_tree_baseline`. Format version 2: full `summary.json`,
`trace.jsonl` payloads, `final.diff`, and fingerprints recomputed from those
files.

This is a regression snapshot of the working tree after the AnalysisLoop split.
It is not a reconstruction of missing historical payloads and must not be
described as proof that refactor-before and refactor-after traces were equal.

Refresh only into a **new** directory:

```text
python -m devtools.session_replay.capture --out tests/fixtures/session_replay/current_v2_next
```

Never point `--out` at `output/analysis_loop_split_0908/runs`.
