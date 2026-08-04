# Real-bug benchmark environment acceptance

Verified: 2026-07-30

All five original candidates and both natural multi-file candidates pass the
environment gate. This report is about task reproducibility only; it contains
no new multi-file model repair results and makes no repair-success claim for
those tasks.

| Task | Project runtime | Buggy result | Fixed result | Public cases |
|---|---:|---|---|---:|
| `pysnooper-3` | Python 3.8.20 | expected `NameError` | pass | 1 |
| `tornado-11` | Python 3.7.17 | uppercase chunk body lost | pass | 1 |
| `tqdm-3` | Python 3.6.15 | expected bool `TypeError` | pass | 1 |
| `sanic-5` | Python 3.8.20 | expected `sanic.root` `KeyError` | pass | 1 |
| `thefuck-19` | Python 3.7.17 | unsafe `--force` output | pass | 3 |
| `tornado-10` | Python 3.7.17 | rendered WebSocket response is `None` | pass | 1 |
| `thefuck-16` | Python 3.7.17 | alias variables scoped outside substitution | pass | 2 |

Each task records its immutable base-image digest, complete resolved dependency
pins, public upstream regression-test patch, expected failure signature, fixed
result, resource limits, and verified local image digest in `acceptance.json`.
Tests run as an unprivileged user with networking disabled, one CPU, 512 MiB of
memory, and a 128-process limit.

## Hidden semantic gate

Hidden tests are stored outside the Agent workspace and injected only after a
repair run. All five original tasks have completed the stricter buggy-fail /
fixed-pass check:

| Task | Hidden semantic variant | Gate |
|---|---|---|
| `pysnooper-3` | `PathLike` output appends without truncation | accepted |
| `tornado-11` | mixed-case `Transfer-Encoding` value | accepted |
| `tqdm-3` | explicit `total` overrides iterable truthiness | accepted |
| `sanic-5` | Sanic logger does not reuse named root logger | accepted |
| `thefuck-19` | complex push arguments retain safe force mode | accepted |

## Natural multi-file candidates

Two BugsInPy tasks are frozen and have passed the same Docker environment gate.
They have no model repair scores yet:

| Task | Product files in reference fix | Size | State |
|---|---:|---:|---|
| `tornado-10` | 2 | 308 files / 1,995,774 bytes | accepted |
| `thefuck-16` | 4 | 226 files / 1,016,520 bytes | accepted |

Their public test patches apply cleanly to the exact buggy commits, both remain
below product import caps, and their image digests are frozen in
`acceptance.json`.

## Reproduce

From the repository root, with Git and Docker available:

```bash
python examples/real_bug_benchmark/verify.py
```

To verify only the multi-file environments explicitly:

```bash
python examples/real_bug_benchmark/verify.py tornado-10 thefuck-16
```

The verifier rebuilds each image, clones the two upstream revisions into a
temporary directory, overlays the public regression test on the buggy revision,
and validates that buggy fails for the recorded reason while fixed passes. When
a hidden patch exists it is applied to both revisions and must also fail on
buggy and pass on fixed. The fixed revision and `acceptance.json` are benchmark
infrastructure; they must not be copied into the Agent workspace or prompt
during model runs.

## Interpretation

Environment acceptance prevents dependency, collection, and runtime migration
errors from being counted as model failures. Historical model results remain
frozen; the next experiment phase is a small, separately reported run on the
two multi-file tasks. Task-level outcomes, public/hidden status, tokens,
latency, failure class, and estimated cost must be reported together.
