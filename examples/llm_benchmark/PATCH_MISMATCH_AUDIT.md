# Patch Context Mismatch Audit (read-only)

**Scope:** Pilot runs `bench05_red_herring` / `bench12_slug_overfit` only.  
**Constraints:** No changes to `code_agent/` or the four pilot task assets.  
**Sources:** `results/<task>/<run_id>/trace.jsonl` + frozen public file contents.

---

## Shared mechanism

`PatchApplier` applies unified diffs with **exact context matching**. A hunk header `@@ -old_start,old_count …` means: starting at 1-based `old_start`, the next context/`-` lines must equal the file verbatim. Mismatch → `Context mismatch in <file> at line N: expected '…', got '…'`.

---

## bench05_red_herring (`run_id=20260727T032555Z_f23d5800`)

### Working copy before apply (unchanged across attempts)

```python
def final_price(amount: float, rate: float) -> float:
    """Return price after discount rate in [0, 1].

    final_price = amount * (1 - rate)
    """
    # BUG: multiplies by rate instead of (1 - rate)
    return amount * rate
```

Line map: L3 blank, **L4** = `    final_price = amount * (1 - rate)`.

### Attempt 1 (failed)

1. **Reads:** `pricing.py` lines 1–20 (full 7-line file returned in tool result).
2. **Diff:**
```diff
--- a/pricing.py
+++ b/pricing.py
@@ -4,5 +4,5 @@
 
     final_price = amount * (1 - rate)
     """
-    # BUG: multiplies by rate instead of (1 - rate)
-    return amount * rate
+    return amount * (1 - rate)
```
3. **Pre-apply file:** as above.
4. **Applier error:** `Context mismatch in pricing.py at line 4: expected '', got '    final_price = amount * (1 - rate)'`
5. **First failing hunk:** the only hunk. Header claims old line 4 is a **blank** context line; real L4 is the docstring formula line. Anchor is off-by-one (blank is L3).
6. **vs success:** Attempt 3 drops the spurious blank context line and starts the hunk at the true L4 (`final_price = …`).
7. **Root-cause class:** **`hallucinated_context`** (incorrect hunk start / invented leading blank in hunk body relative to header).
8. **Ownership:** **Model generation quality** (primary). Not asset bug (file is stable and was correctly shown by `read_file`). Applier behavior is expected strict matching — not “too strict” relative to unified-diff semantics. **Not** benchmark asset issue.

### Attempt 2 (failed)

Same read range; **identical broken hunk** as attempt 1; same error. Model did not correct the anchor after apply failure feedback.

### Attempt 3 (success)

```diff
@@ -4,5 +4,5 @@
     final_price = amount * (1 - rate)
     """
     # BUG: multiplies by rate instead of (1 - rate)
-    return amount * rate
+    return amount * (1 - rate)
```

Context now matches L4–L7; only the `return` line changes (comment left). Apply OK → public+hidden pass.

---

## bench12_slug_overfit (`run_id=20260727T032624Z_8fa76e59`)

### Working copy before apply

```text
L8:     - collapse consecutive '-'
L9:     - strip leading/trailing '-'
L10:    - empty / all-stripped input -> ""
L11:    """
L12:    # BUG: ...
L13:    return title.lower()
```

### Attempt 1 (failed)

1. **Reads:** `web.py` 1–80 (full file), `tests/test_slug.py` 1–30, `textnorm.py` 1–30.
2. **Diff:**
```diff
--- a/web.py
+++ b/web.py
@@ -9,5 +9,10 @@
     - empty / all-stripped input -> ""
     """
-    # BUG: only lowercases; ignores punctuation/spaces rules
-    return title.lower()
+    import re
+    ...
```
3. **Pre-apply file:** as above.
4. **Applier error:** `Context mismatch in web.py at line 9: expected '    - empty / all-stripped input -> ""', got "    - strip leading/trailing '-'"`
5. **First failing hunk:** only hunk. Header `@@ -9,5` claims L9 is the “empty / all-stripped” bullet; real L9 is “strip leading/trailing”. Model **skipped** one docstring bullet when choosing the anchor (off-by-one in docstring list).
6. **vs success:** Attempt 2 uses `@@ -10,4` and correctly anchors on the empty-input bullet (true L10), then replaces L12–L13.
7. **Root-cause class:** **`hallucinated_context`** (wrong line number / omitted intermediate context line).
8. **Ownership:** **Model generation quality** (primary). Reads were accurate; assets fine; applier correctly rejected bad context. Minor note: long docstring lists increase model risk of off-by-one hunks — still not an asset defect.

### Attempt 2 (success)

Correct anchor at L10; body rewrite applies; public+hidden pass.

---

## Cross-cutting conclusions

| Question | Answer |
|---|---|
| Dominant class | `hallucinated_context` (wrong hunk line / extra or missing context line) |
| `newline_mismatch`? | No evidence (errors cite wrong *content*, not CRLF alone) |
| `stale_context`? | No — working_copy unchanged between failed attempts |
| `invalid_diff_format`? | No — headers parse; apply reaches context check |
| `patch_applier_too_strict`? | **No** for these cases; matches standard unified-diff expectations |
| `excessive_context`? | Not primary; problem is **wrong** context, not too much |
| Product change needed now? | **No** (audit-only; freeze holds) |
| Benchmark asset change needed? | **No** for these four tasks |

**Product implication (for later, not this change set):** many repair attempts here are **apply failures**, not test failures — track `patch_apply_*` metrics separately from `repair_attempts`.

---

## Metric definitions added (benchmark infra only)

See `metrics_lib.py`:

- `patch_proposals` — approved `propose_patch` proposals that reached apply (`patch_applied` events)
- `patch_apply_failures` — `patch_applied` with `ok=false`
- `patch_apply_success_rate` — successes / (successes + failures) among approved applies; `null` if none
- `first_patch_applicable` — whether the first `patch_applied` in the session succeeded

`repair_attempts` remains the product `summary.attempts_used` semantics unchanged.
