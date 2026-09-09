# Release checklist

Use this checklist for a specific commit. A green result from an earlier
checkout is not release evidence for a later one.

1. Confirm a clean working tree, intended package version and tag.
2. Run all groups in [Development](DEVELOPMENT.md): offline, required Docker,
   packaging, Ruff, mypy and diff checks.
3. Run the three [published-evidence verifiers](EVALUATION.md).
4. Build a wheel and install it in a clean environment outside the checkout.
   Verify `safepatch --help`, `Dockerfile.pytest` and the packaged bootstrap/plugin.
5. Check that the [README](../README.md), [pytest support](PYTEST_SUPPORT.md) and
   CLI agree, and that local credentials and runtime output are excluded.
6. Record commit SHA, Python versions, Docker image digest and CI links with
   the release. State skipped checks and known limits.

Publishing a release is a separate action from passing these checks. Historical
model results retain their original scope and are not rewritten as new results.
