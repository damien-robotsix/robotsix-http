"""Keep ``docs/configuration.md`` in sync with the code it documents.

The configuration reference restates field names and default values by
hand.  These tests parse its markdown tables and compare them with the
real ``RetryConfig`` dataclass fields and ``create_health_router``
signature, so changing a default or adding a field without updating the
page fails the suite.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from pathlib import Path
from typing import Any

import pytest

from robotsix_http.retry import RetryConfig

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "configuration.md"

# Split table rows on ``|`` but not on an escaped ``\|`` (used in type columns).
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def _parse_table_after(marker: str) -> list[dict[str, str]]:
    """Return the rows of the first markdown table following *marker*.

    Each row is a mapping of header → cell text, with surrounding
    backticks and whitespace stripped.
    """
    lines = DOC_PATH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    table: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in _CELL_SPLIT.split(stripped)[1:-1]]
            table.append(cells)
        elif table:
            break
    assert len(table) >= 2, f"no table found after {marker!r}"
    header, _separator, *rows = table
    return [{h: _strip_code(c) for h, c in zip(header, row, strict=True)} for row in rows]


def _strip_code(cell: str) -> str:
    cell = cell.strip()
    if len(cell) >= 2 and cell.startswith("`") and cell.endswith("`"):
        cell = cell[1:-1]
    return cell


def _doc_default(text: str) -> Any:
    """Evaluate a documented default written as a Python literal."""
    return ast.literal_eval(text)


def test_retry_config_table_matches_dataclass() -> None:
    rows = _parse_table_after("## Retry — `RetryConfig`")
    fields = dataclasses.fields(RetryConfig)

    assert [r["Field"] for r in rows] == [f.name for f in fields]
    for row, field in zip(rows, fields, strict=True):
        assert _doc_default(row["Default"]) == field.default, (
            f"RetryConfig.{field.name}: docs say {row['Default']}, code has {field.default!r}"
        )


def test_health_router_table_matches_signature() -> None:
    pytest.importorskip("fastapi")
    from robotsix_http.fastapi import create_health_router

    rows = _parse_table_after("`create_health_router(...)` parameters")
    params = list(inspect.signature(create_health_router).parameters.values())

    assert [r["Parameter"] for r in rows] == [p.name for p in params]
    for row, param in zip(rows, params, strict=True):
        assert _doc_default(row["Default"]) == param.default, (
            f"create_health_router({param.name}=...): docs say {row['Default']}, "
            f"code has {param.default!r}"
        )
