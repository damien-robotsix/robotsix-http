# robotsix-http

Shared HTTP retry/backoff library for the robotsix ecosystem.

## Structure

- `src/robotsix_http/` — library source
- `tests/` — pytest suite (80% coverage minimum)
- `docs/modules.yaml` — module taxonomy

> **Rule:** New test files live per-module under `tests/<module-id>/` mirroring the `modules` taxonomy in `docs/modules.yaml` (e.g. library tests go in `tests/core/`). Do not add flat test files at the `tests/` root; keep `tests/__init__.py` as the only package-marker file at that root.
>
> **Rationale:** Established by ticket 20260731T182943Z (PR #50): flat `tests/test_client.py`/`test_retry.py`/`test_version.py` were reorganized into `tests/core/` and `docs/modules.yaml` narrowed the `tests` module glob from `tests/**` to `tests/__init__.py` with `tests/core/**` assigned to the `core` module. A contributor adding tests should follow this per-module layout so `docs/modules.yaml` path ownership stays accurate.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check
uv run mypy src/ --strict
```

## Changelog / release

- The project uses [release-please](https://github.com/googleapis/release-please) with a
  **static version** in `pyproject.toml`.  Do not bump the version by hand — release-please
  bumps it automatically on release PRs.
- Changelog entries are generated from **conventional commit messages**:
  `feat:` triggers a minor bump, `fix:` triggers a patch bump, and `feat!:` or a `BREAKING CHANGE`
  footer triggers a major bump.  `chore:`, `docs:`, `style:`, `refactor:`, `perf:`, `test:`, and
  `ci:` are ignored for versioning but still appear in the changelog.
- Write descriptive commit messages — the first line of each merged commit becomes the
  changelog bullet for that release.
- Do **not** create `changelog.d/` towncrier fragments (`.feature.md` / `.bugfix.md` /
  `.misc.md` / `.breaking.md`).  The repo migrated from towncrier to release-please and
  those fragments are silently ignored.

## CI

Uses `damien-robotsix/robotsix-github-workflows` reusable workflows.

> **Rule:** Never install a binary downloaded in `.github/workflows/` without verifying it against a SHA-256 pinned in the workflow file (env var), using `sha256sum -c` before extraction or install. Do not rely on a checksum file fetched from the same origin as the binary.
>
> **Rationale:** Observed in robotsix-http: the gitleaks CI job (ticket 20261016T061630Z-add-a-secret-scanner-gitleaks-to-pre-com-4251) downloaded and installed a security-gate binary with no integrity check; the follow-up 20261022T115214Z-verify-gitleaks-release-checksum-in-ci-g-bf34 (PR #155) added the pinned check. Single occurrence so far; the rule is recorded as a convention for human contributors adding CI tooling.
