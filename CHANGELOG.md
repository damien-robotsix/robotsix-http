# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- towncrier release notes start -->

## [0.3.2](https://github.com/damien-robotsix/robotsix-http/compare/v0.3.1...v0.3.2) (2026-08-23)


### Bug Fixes

* **retry:** stop retrying POST/PATCH after the request may have been delivered ([#85](https://github.com/damien-robotsix/robotsix-http/issues/85)) ([13e6e9a](https://github.com/damien-robotsix/robotsix-http/commit/13e6e9ab330f9be3b8b1e7bc2694210f7d9e5848))

## [0.3.1](https://github.com/damien-robotsix/robotsix-http/compare/v0.3.0...v0.3.1) (2026-08-09)


### Features

* **retry:** add `stop_after_delay` wall-clock deadline to `RetryConfig` ([#64](https://github.com/damien-robotsix/robotsix-http/issues/64)) ([3c752ee](https://github.com/damien-robotsix/robotsix-http/commit/3c752ee53fc83e38ad36c6bee0496375d1366fea))


### Bug Fixes

* **release:** don't fail lock-sync when the release branch is gone ([#75](https://github.com/damien-robotsix/robotsix-http/issues/75)) ([4466330](https://github.com/damien-robotsix/robotsix-http/commit/44663307fc076c4572845072b517627e417d04a0))

## [0.3.0](https://github.com/damien-robotsix/robotsix-http/compare/v0.2.0...v0.3.0) (2026-08-08)


### Features

* **release:** static version and release-please, drop hatch-vcs ([#69](https://github.com/damien-robotsix/robotsix-http/issues/69)) ([8d0aa1a](https://github.com/damien-robotsix/robotsix-http/commit/8d0aa1a4a78c5fe4366e23888547e9d95272f006))


### Bug Fixes

* **release:** mint an App token so release PRs get CI ([#71](https://github.com/damien-robotsix/robotsix-http/issues/71)) ([2d6abf8](https://github.com/damien-robotsix/robotsix-http/commit/2d6abf8fc82efd0366e3c9e09674e4f54560ff6e))
* **release:** restore the App token and sync uv.lock ([#74](https://github.com/damien-robotsix/robotsix-http/issues/74)) ([8f4e482](https://github.com/damien-robotsix/robotsix-http/commit/8f4e482b4344bffbbe08247b60dcdbfc29e75226))
* **retry:** restore wrapped-JSONDecodeError and APITimeoutError as transient ([#45](https://github.com/damien-robotsix/robotsix-http/issues/45)) ([ba90d55](https://github.com/damien-robotsix/robotsix-http/commit/ba90d55ea2bcbd02f605f392e837cea7b485d73d))

## 0.2.0 (2026-08-08)

### Features

- Added ``on_retry_exhausted`` callback to ``RetryConfig``, invoked before the final raise when retries are exhausted or a non-retryable error is encountered. Receives ``(attempt, exception)`` with the 1-indexed total attempt count.

### Miscellaneous

- Add RetryClient-level test for on_retry_exhausted firing on the non-idempotent rejection branch (POST/PATCH + 5xx/429)
- Fix truncated content in two unreleased changelog fragments (20260731)
- Pin `httpx>=0.27,<1.0` and add `Typing :: Typed` classifier in robotsix-http pyproject.toml
- Format except clause per ruff formatter: remove unnecessary parentheses around ``except (ValueError, TypeError):`` → ``except ValueError, TypeError:`` (both forms are equivalent in Python 3.14+).
- Add .robotsix-mill/config.yaml to robotsix-http declaring languages: [python]
- Enable pytest filterwarnings=["error"] in robotsix-http pyproject.toml
- Add on_retry_exhausted give-up callback to RetryConfig (close the retry-observability gap)
- Add internal DEBUG logging to robotsix_http retry/client (getLogger(__name__) + NullHandler)
- robotsix-http: Enable mypy_baseline periodic workflow
- Classify .robotsix-mill/config.yaml: assign to existing module or propose a new one
- Add regression test for Python 2-style `except` import fix in client.py
- AGENT.md: Changelog / release — User-visible changes go in `changelog.d/` towncrier fragments (`.feature.md` / `.bugfix.md` / `.misc.md` / `.breaking.md`). Never edit CHANGELOG.md directly.
- Make test_import_clean actually detect import failures
- AGENT.md: Structure — New test files live per-module under `tests/<module-id>/` mirroring the `modules` taxonomy in `docs/modules.yaml`.


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

- Internal debug logging: `robotsix_http.retry` and `robotsix_http.client` now emit DEBUG records on retry and exhaustion, using lazy %-formatting. The package logger registers a `NullHandler` so no output is emitted unless the caller configures logging.
- Added `.robotsix-mill/config.yaml` with `languages: [python]` to declare the repo's language scope.
- Enable pytest `filterwarnings = ["error"]` in pyproject.toml to turn warnings into hard test failures.
- Format except clause per ruff formatter: remove unnecessary parentheses around ``except (ValueError, TypeError):`` → ``except ValueError, TypeError:`` (both forms are equivalent in Python 3.14+).
- Pin httpx to `<1.0` (`>=0.27,<1.0`) to protect against breaking changes in the pre-1.0 release series, and add the `Typing :: Typed` trove classifier.
- Document towncrier fragment workflow in `AGENT.md` under a new "Changelog / release" section.
- Add test-file layout rule to `AGENT.md` ## Structure: new tests live per-module under `tests/<module-id>/` mirroring `docs/modules.yaml`.
- Reorganize `tests/` into per-module layout: move `test_client.py`, `test_retry.py`, and `test_version.py` into `tests/core/`.
