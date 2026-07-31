# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.0.0 (unreleased)

- Enable `module_curator` periodic workflow to keep `docs/modules.yaml` in sync with the live directory tree.
- Add `check-merge-conflict` and `detect-private-key` pre-commit hooks from `pre-commit/pre-commit-hooks`.
- Update pre-commit hook revisions: ruff to v0.15.20, mypy to v2.1.0 to match uv.lock.
- Added `test_head_retries_on_503` and `test_options_retries_on_503` to `TestIsRetryableForMethod` in `tests/test_client.py`
- Replace two narrow no-break space characters (U+202F) with regular spaces in `RetryConfig.jitter_factor` docstring and add `"RUF"` to ruff lint select to catch similar issues.
- Remove `exclude-newer` from `[tool.uv]` in `pyproject.toml` — the
  date-based filter was blocking Dependabot from resolving
  recently-published package versions (e.g. mypy 2.3.0). The lockfile
  already provides reproducible resolution.
- Increase `exclude-newer` from 7d to 30d to prevent Dependabot from
  failing to resolve recently-published package versions (e.g. ruff 0.16.0).
- Update `[tool.ruff] target-version` from ``py312`` to ``py314`` and `[tool.mypy] python_version` from ``3.12`` to ``3.14`` to match ``requires-python = ">=3.14"``.
- Document `.put()`, `.head()`, and `.options()` convenience methods in the `RetryClient` API overview table row.
- Add `exclude-newer = "7 days"` to `[tool.uv]` in `pyproject.toml` for supply-chain hardening
- Remove misleading Python 3.12 and 3.13 classifiers from `pyproject.toml` — the package requires Python ≥3.14.
- Fix stale docstring in `_status()` that referenced a removed ``exc.response.status`` fallback.
- Removed misleading `(idempotency-gated)` annotation from `put()` and `options()` docstrings — PUT and OPTIONS are already in `_SAFE_METHODS` and retried on all transient errors, matching `get()`, `delete()`, and `head()`.
- Enable `module_size` periodic workflow to monitor module sizes and catch bloat before modules exceed 500 lines.
- Add `robotsix-modules` dev dependency (pinned git source) for module taxonomy validation.
- Add `deptry` hook to `.pre-commit-config.yaml` for early detection of
  import/dependency issues before commit, matching the CI `run-deptry` check.
- Remove unreachable `.status` fallback from `_status()` in retry module.
  Add test covering the cause-chain `TransportError` path in `is_transient()`.
- Enable Ruff security rules (`S`) in lint configuration, silencing pre-existing false positives in retry and client modules
- Add standard repo-hygiene pre-commit hooks (trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-added-large-files).
- Enable `copy_paste` periodic workflow for jscpd-based duplication detection
- Enable bc_check periodic workflow by adding `.robotsix-mill/periodic/bc_check.yaml`.
- Add `put`, `head`, and `options` convenience methods to `RetryClient`.
- Fixed structural corruption in `.github/dependabot.yml` where the `uv` entry was incorrectly placed inside the `github-actions` block instead of as a sibling entry, and restored the `github-actions` properties that were displaced
- Add `py.typed` marker file for PEP 561 compliance, enabling downstream type checkers to use inline annotations.
- Expanded `README.md` with installation instructions, quick-start usage examples, API overview, and idempotency gating documentation.
- Extract the duplicate ``_invoke`` inner function from ``call_with_retry`` and ``acall_with_retry`` into a module-level ``_invoke`` helper, and extract the shared ``cfg``/``transient`` resolution into ``_resolve_config``.
- Add `test_gap` periodic workflow to detect coverage regressions
- Enable health periodic workflow by adding `.robotsix-mill/periodic/health.yaml`.
- Add `robotsix_http.client` module with async `RetryClient` wrapping `httpx.AsyncClient`, providing method-idempotency gates, `Retry-After` header support, and a typed exception hierarchy (`ExternalHTTPError`, `ExternalAuthError`, `ExternalRateLimitError`, `ExternalServiceError`).
- Add `robotsix_http.retry` module with domain-neutral retry primitives: `RetryConfig`, `call_with_retry`, `acall_with_retry`, `is_transient`, and internal helpers for cause-chain walking, status extraction, and exponential-backoff computation with jitter.
- Initial scaffold of robotsix-http library: pyproject.toml with hatchling backend, CI via robotsix-github-workflows, dependabot, skeleton docs.
