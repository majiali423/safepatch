# tqdm bug 3

Boolean conversion must work for tqdm objects backed by sized and unsized
iterables, and must be undefined only when neither an iterable nor a total is
available. A benchmark-owned public regression test is applied as a public
overlay. Keeping it outside the historical upstream test module makes the task
reproducible even when that module's surrounding context differs by revision.

The Agent receives the full buggy repository plus the public test overlay. It
does not receive `acceptance.json`, the fixed commit, or the reference patch.
