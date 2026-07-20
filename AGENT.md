# robotsix-http

Shared HTTP retry/backoff library for the robotsix ecosystem.

## Structure

- `src/robotsix_http/` — library source
- `tests/` — pytest suite (80% coverage minimum)
- `docs/modules.yaml` — module taxonomy

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check
uv run mypy src/ --strict
```

## CI

Uses `damien-robotsix/robotsix-github-workflows` reusable workflows.
