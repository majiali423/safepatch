# PySnooper bug 3

When `@pysnooper.snoop(...)` receives a filesystem path, tracing should append
to that path. The public upstream regression test is supplied separately from
the buggy commit through `public_test.patch`.

The Agent receives the full buggy repository plus the public test overlay. It
does not receive `acceptance.json`, the fixed commit, or the reference patch.
