# Tornado bug 11

HTTP transfer-coding values are case-insensitive. A request using
`Transfer-Encoding: Chunked` must therefore be decoded like lowercase
`chunked`. The public upstream regression test is supplied through
`public_test.patch`.

The Agent receives the full buggy repository plus the public test overlay. It
does not receive `acceptance.json`, the fixed commit, or the reference patch.
