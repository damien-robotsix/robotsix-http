"""Keep the hand-written configuration docs in sync with the code.

``docs/core/configuration.md`` and ``docs/core/fastapi-bootstrap.md``
restate option names and default values by hand.  These tests parse their
markdown tables and compare them with the code:

* the ``RetryConfig`` table against the dataclass fields and defaults;
* the URL/IP safety and OAuth2 tables against the signatures of every
  callable listed in their ``Where`` column (``validate_url``,
  ``safe_http_request``, ``guarded_async_client``, ``SSRFGuardTransport``,
  ``write_secret_file``, ``read_secret_file``, ``SecureTokenStore``) and
  the ``ALLOWED_SCHEMES`` module constant;
* the ``create_health_router(...)`` parameters table and the
  fastapi-bootstrap route table against ``create_health_router``;
* the "Other FastAPI-side defaults" table against ``DomainError``, the
  correlation-ID middleware and ``create_chat_skill_router``;
* the logger-names table against the modules that create a logger.

A documented default that is a bare identifier (e.g. ``ALLOWED_SCHEMES``)
is resolved on the documented module; anything else is evaluated as a
Python literal.  A few defaults are prose by design (e.g. the correlation
``generator``); those rows are listed in ``_DESCRIPTIVE_DEFAULTS`` and only
their parameter names are checked.  Changing a default, or adding a
keyword-only parameter or dataclass field, without updating the page
fails the suite.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import keyword
import logging
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import robotsix_http
from robotsix_http.retry import RetryConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "core" / "configuration.md"
FASTAPI_DOC_PATH = REPO_ROOT / "docs" / "core" / "fastapi-bootstrap.md"
SRC_DIR = REPO_ROOT / "src" / "robotsix_http"

# Split table rows on ``|`` but not on an escaped ``\|`` (used in type columns).
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_CODE_SPAN = re.compile(r"`([^`]*)`")
_NO_DEFAULT = "—"


def _parse_table_after(marker: str, doc_path: Path = DOC_PATH) -> list[dict[str, str]]:
    """Return the rows of the first markdown table following *marker*.

    When *marker* matches a table row (e.g. the header row itself), that
    table is returned.  Each row is a mapping of header → cell text, with
    surrounding backticks and whitespace stripped.
    """
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    if not lines[start].strip().startswith("|"):
        start += 1
    table: list[list[str]] = []
    for line in lines[start:]:
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


def _doc_default(text: str, namespace: ModuleType | None = None) -> Any:
    """Evaluate a documented default.

    A bare identifier (other than a keyword such as ``None``) is resolved
    with ``getattr`` on *namespace*; ``frozenset(<literal>)`` is built from
    its literal argument; anything else is evaluated as a Python literal.
    """
    if namespace is not None and text.isidentifier() and not keyword.iskeyword(text):
        assert hasattr(namespace, text), f"{namespace.__name__} has no attribute {text!r}"
        return getattr(namespace, text)
    if text.startswith("frozenset(") and text.endswith(")"):
        return frozenset(ast.literal_eval(text[len("frozenset(") : -1]))
    return ast.literal_eval(text)


def _documented_callables(cell: str, module: ModuleType) -> list[tuple[str, Any]]:
    """Return the ``(name, object)`` pairs of *cell* identifiers defined in *module*."""
    found = []
    for name in _IDENTIFIER.findall(cell):
        obj = getattr(module, name, None)
        if obj is not None and callable(obj):
            found.append((name, obj))
    return found


def _param_name(param: inspect.Parameter) -> str:
    return f"**{param.name}" if param.kind is inspect.Parameter.VAR_KEYWORD else param.name


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


@pytest.mark.parametrize(
    ("marker", "module_name", "expected_callables"),
    [
        (
            "## URL/IP safety",
            "robotsix_http.safety",
            {"validate_url", "safe_http_request", "guarded_async_client", "SSRFGuardTransport"},
        ),
        (
            "## OAuth2 persistence",
            "robotsix_http.oauth",
            {"write_secret_file", "read_secret_file", "SecureTokenStore"},
        ),
    ],
)
def test_option_table_matches_signatures(
    marker: str, module_name: str, expected_callables: set[str]
) -> None:
    module = importlib.import_module(module_name)
    rows = _parse_table_after(marker)

    documented: dict[str, set[str]] = {}
    for row in rows:
        option, where, default = row["Option"], row["Where"], row["Default"]
        if where == "module constant":
            assert _doc_default(default) == getattr(module, option), (
                f"{module_name}.{option}: docs say {default}, code has {getattr(module, option)!r}"
            )
            continue
        callables = _documented_callables(where, module)
        assert callables, f"{option}: no callable of {module_name} listed in {where!r}"
        for name, obj in callables:
            documented.setdefault(name, set()).add(option)
            params = {_param_name(p): p for p in inspect.signature(obj).parameters.values()}
            assert option in params, f"{name} has no parameter {option!r}"
            param = params[option]
            if default == _NO_DEFAULT:
                assert param.default is inspect.Parameter.empty, (
                    f"{name}({option}=...): docs say no default, code has {param.default!r}"
                )
            else:
                assert _doc_default(default, module) == param.default, (
                    f"{name}({option}=...): docs say {default}, code has {param.default!r}"
                )

    assert set(documented) == expected_callables
    # Every keyword-only parameter (and ``**kwargs``) must be documented.
    for name, options in documented.items():
        sig = inspect.signature(getattr(module, name))
        keyword_params = {
            _param_name(p)
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.VAR_KEYWORD)
        }
        assert keyword_params <= options, (
            f"{name}: undocumented parameters {sorted(keyword_params - options)}"
        )


# (symbol, option) rows of the "Other FastAPI-side defaults" table whose
# default is described in prose rather than written as a literal.
_DESCRIPTIVE_DEFAULTS = {
    ("CorrelationIdMiddleware", "generator"),
    ("CorrelationIdMiddleware", "logger"),
    ("create_correlation_id_middleware", "generator"),
    ("create_correlation_id_middleware", "logger"),
    ("assert_chat_skill_route_parity", "ignored routes"),
}


def test_fastapi_defaults_table_matches_code() -> None:
    pytest.importorskip("fastapi")
    fastapi_module = importlib.import_module("robotsix_http.fastapi")

    rows = _parse_table_after("Other FastAPI-side defaults:")
    symbols: list[tuple[str, Any]] = []
    checked: dict[str, set[str]] = {}
    for row in rows:
        if row["Symbol"]:
            symbols = _documented_callables(row["Symbol"], fastapi_module)
            assert symbols, f"no robotsix_http.fastapi symbol in {row['Symbol']!r}"
        options = [o.strip(" `") for o in row["Option"].split(" / ")]
        spans = _CODE_SPAN.findall(row["Default"])
        for name, obj in symbols:
            for index, option in enumerate(options):
                checked.setdefault(name, set()).add(option)
                if (name, option) in _DESCRIPTIVE_DEFAULTS:
                    if option.isidentifier():
                        assert option in inspect.signature(obj).parameters, (
                            f"{name} has no parameter {option!r}"
                        )
                    continue
                doc_value = _doc_default(spans[index] if spans else row["Default"])
                if inspect.isclass(obj) and option in vars(obj):
                    code_value = vars(obj)[option]
                else:
                    params = inspect.signature(obj).parameters
                    assert option in params, f"{name} has no parameter {option!r}"
                    code_value = params[option].default
                assert doc_value == code_value, (
                    f"{name}.{option}: docs say {row['Default']}, code has {code_value!r}"
                )

    assert {"DomainError", "CorrelationIdMiddleware", "create_correlation_id_middleware"} <= set(
        checked
    )
    assert "create_chat_skill_router" in checked
    for name in ("CorrelationIdMiddleware", "create_correlation_id_middleware"):
        sig = inspect.signature(getattr(fastapi_module, name))
        keyword_params = {
            p.name for p in sig.parameters.values() if p.kind is inspect.Parameter.KEYWORD_ONLY
        }
        assert keyword_params <= checked[name], (
            f"{name}: undocumented parameters {sorted(keyword_params - checked[name])}"
        )
    chat_sig = inspect.signature(fastapi_module.create_chat_skill_router)
    chat_keyword = {
        p.name for p in chat_sig.parameters.values() if p.kind is inspect.Parameter.KEYWORD_ONLY
    }
    assert chat_keyword == checked["create_chat_skill_router"]


_ROUTE_PARAMS = {
    "health": "path",
    "liveness": "live_path",
    "startup": "startup_path",
    "readiness": "ready_path",
}


def test_fastapi_bootstrap_route_table_matches_signature() -> None:
    pytest.importorskip("fastapi")
    from robotsix_http.fastapi import create_health_router

    rows = _parse_table_after("| Route | Default path |", FASTAPI_DOC_PATH)
    params = inspect.signature(create_health_router).parameters

    assert {r["Route"] for r in rows} == set(_ROUTE_PARAMS)
    for row in rows:
        param = params[_ROUTE_PARAMS[row["Route"]]]
        cell = row["Default path"]
        doc_value = None if cell.startswith("disabled") else cell
        if doc_value is None:
            assert f"`{param.name}=None`" in cell, f"{row['Route']}: unexpected cell {cell!r}"
        assert doc_value == param.default, (
            f"{row['Route']} route ({param.name}): docs say {cell}, code has {param.default!r}"
        )


def test_logger_names_table_matches_modules() -> None:
    rows = _parse_table_after("## Logger names")
    stdlib = {r["Logger"] for r in rows if r["Library"] == "stdlib `logging`"}
    structlog_names = {r["Logger"] for r in rows if r["Library"] == "structlog"}

    assert robotsix_http.__name__ in stdlib
    root = logging.getLogger(robotsix_http.__name__)
    assert any(isinstance(h, logging.NullHandler) for h in root.handlers)

    module_loggers = {
        f"robotsix_http.{path.stem}"
        for path in SRC_DIR.glob("*.py")
        if "logging.getLogger(__name__)" in path.read_text(encoding="utf-8")
        and path.stem != "__init__"
    }
    assert stdlib - {robotsix_http.__name__} == module_loggers

    fastapi_source = (SRC_DIR / "fastapi.py").read_text(encoding="utf-8")
    for name in structlog_names:
        assert f'structlog.get_logger("{name}")' in fastapi_source
