# Session replay fixtures

- `historical_v1`: frozen AnalysisLoop-split snapshot (event names and selected
  counters). Not full trace payload proof.
- `current_v2`: current-version full `summary` / `trace.jsonl` / `final.diff`
  baseline. Not a claim that refactor-before payloads matched.

Tools: `python -m devtools.session_replay.capture --out NEWDIR` and
`python -m devtools.session_replay.compare --baseline ... --after ...`.
Existing `--out` paths are rejected.
