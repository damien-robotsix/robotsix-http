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

## CI

Uses `damien-robotsix/robotsix-github-workflows` reusable workflows.
