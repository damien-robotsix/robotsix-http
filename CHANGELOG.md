# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- towncrier release notes start -->

## 0.1.0 (2026-07-31)

### Features

- Add `robotsix_http.client` module with async `RetryClient` wrapping `httpx.AsyncClient`, providing method-idempotency gates, `Retry-After` header support, and a typed exception hierarchy (`ExternalHTTPError`, `ExternalAuthError`, `ExternalRateLimitError`, `ExternalServiceError`).
- Add `put`, `head`, and `options` convenience methods to `RetryClient`, documented in the API overview table.
- Initial scaffold of robotsix-http library: pyproject.toml with hatchling backend, CI via robotsix-github-workflows, dependabot, skeleton docs.

### Bug Fixes

- Fix stale docstring in `_status()` that referenced a removed `exc.response.status` fallback. Remove misleading `(idempotency-gated)` annotation from `put()` and `options()` docstrings. Fix structural corruption in `.github/dependabot.yml`. Replace narrow no-break space characters in `RetryConfig.jitter_factor` docstring.

### Miscellaneous

- Add `test_head_retries_on_503` and `test_options_retries_on_503` to retry test suite. Add test covering the cause-chain `TransportError` path in `is_transient()`. Remove unreachable `.status` fallback from `_status()` in retry module.
- Add standard repo-hygiene pre-commit hooks. Enable Ruff security rules (`S`). Add `deptry` hook to pre-commit config. Add `robotsix-modules` dev dependency. Update ruff target-version and mypy python_version to 3.14. Enable periodic workflows: module_curator, module_size, copy_paste, bc_check, test_gap, health. Remove `exclude-newer` from pyproject.toml.
- Adopt towncrier changelog fragments + robotsix-auto-release workflow

## 0.0.0 (unreleased)

- Document towncrier fragment workflow in `AGENT.md` under a new "Changelog / release" section.
- Add test-file layout rule to `AGENT.md` ## Structure: new tests live per-module under `tests/<module-id>/` mirroring `docs/modules.yaml`.
- Reorganize `tests/` into per-module layout: move `test_client.py`, `test_retry.py`, and `test_version.py` into `tests/core/`.
