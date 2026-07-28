# Sanic bug 5

Sanic's default logging configuration must use its own root logger namespace so
that changing the Sanic root level does not collide with Python's root logger.
The exposing upstream test is applied as a public overlay.

The Agent receives the full buggy repository plus the public test overlay. It
does not receive `acceptance.json`, the fixed commit, or the reference patch.
