# Safe exact-patch recovery

SafePatch reduces `PATCH_NOT_APPLICABLE` without fuzzy application. Recovery is based on current file contents and remains behind the existing policy, approval, rollback, and test gates.

## Context mismatch diagnostics

A failed hunk reports the error kind, file, hunk index, requested line, first expected line, actual line, exact-match candidate lines, a numbered current-file excerpt, patch hash, working-tree hash, and the precise `read_file` range required before retrying.

## Unique exact relocation

If a hunk does not match at its declared line, the matcher searches the current file for the complete old hunk block: context plus deleted lines. It relocates only when that complete block occurs exactly once and does not overlap an earlier hunk. Zero or multiple matches are rejected. Preflight and apply use the same matcher and record requested and applied lines.

This is not fuzzy apply: similarity, whitespace approximation, partial context, cross-file matching, and ambiguous matches are never accepted.

## Required refresh after stale context

After a recoverable preflight mismatch, the session records a required file and inclusive line range. A new proposal is blocked until `read_file` covers that entire range. Searches, another file, or a partial read do not clear the gate. The mandated recovery read remains available even when the normal read budget is exhausted.

## Read-budget decision gate

Every successful or failed exploration read reports usage against a fixed 12-call budget. At 3, 2, and 1 remaining actions, the result warns the model to reserve reads for essential context. At zero the controller transitions from `EXPLORE` to `SYNTHESIZE`; ordinary read, search, tree, and map calls are closed.

`SYNTHESIZE` accepts a proposal, `finish`, or at most two structured `request_evidence` calls. Evidence requests must identify an explicit unseen range of at most 120 lines, an unanswered task requirement, and why that range resolves it. Accepted evidence returns to `SYNTHESIZE`; it never reopens free exploration. A preflight-required recovery read remains independently allowed.

One evidence parameter error receives a correction opportunity without consuming evidence allowance. Later parameter mistakes, repeated ranges, and ordinary inspection attempts during synthesis are classified as no progress; two consecutive no-progress actions terminate as `READ_BUDGET_EXHAUSTED`. Requesting evidence after both allowances are consumed is a separately traced hard violation.

The session summary records phase, evidence usage, parameter corrections, no-progress counts, and hard violations. Trace events record every phase transition and evidence decision; the terminal remains CLI exit code 8.

## Structured exact replacement

`propose_edit` accepts one or more edits with:

```json
{
  "path": "src/module.py",
  "old_text": "exact current block",
  "new_text": "replacement block",
  "base_revision": "sha256:..."
}
```

Each `old_text` must occur exactly once after normalizing only the platform newline encoding. SafePatch converts the edits to a normal unified-diff `PatchProposal`; it does not create a direct-write bypass. The generated proposal then passes through path and size policy, test-integrity policy, preflight, approval, rollback-capable apply, and pytest.

## File revisions

Every `read_file` result contains a SHA-256 revision of the exact file bytes. Structured edits require that revision. A proposal can also carry `base_revisions` for unified-diff changes. Proposal construction, preflight, and apply reject a stale revision even when the requested old text still exists.

Human approval remains bound to the full patch hash and working-tree hash, providing a final whole-tree race check in addition to per-file revisions.

## Safety invariants

- No fuzzy or similarity-based application.
- No automatic choice among multiple exact matches.
- No cross-file relocation.
- No proposal before a required stale-region refresh.
- No structured edit based on a stale file revision.
- No write before policy and preflight succeed.
- Multi-file apply remains transactional with rollback on failure.
