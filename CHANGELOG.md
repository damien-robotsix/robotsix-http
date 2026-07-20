# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.0.0 (unreleased)

- Add `robotsix_http.retry` module with domain-neutral retry primitives: `RetryConfig`, `call_with_retry`, `acall_with_retry`, `is_transient`, and internal helpers for cause-chain walking, status extraction, and exponential-backoff computation with jitter.
- Initial scaffold of robotsix-http library: pyproject.toml with hatchling backend, CI via robotsix-github-workflows, dependabot, skeleton docs.
