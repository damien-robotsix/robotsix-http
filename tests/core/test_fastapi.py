"""Tests for robotsix_http.fastapi (service-bootstrap helpers).

The handlers and the health route are exercised directly (without
``fastapi.testclient.TestClient``) so the suite does not couple to the
Starlette test-client's httpx integration.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from robotsix_http import (
    ExternalAuthError,
    ExternalRateLimitError,
    ExternalServiceError,
)
from robotsix_http.client import ExternalHTTPError
from robotsix_http.fastapi import (
    ChatSkillFrontmatter,
    DomainError,
    app_route_paths,
    assert_chat_skill_route_parity,
    create_chat_skill_router,
    create_health_router,
    documented_routes,
    domain_error_handler,
    error_envelope,
    external_http_error_handler,
    http_exception_handler,
    parse_chat_skill_frontmatter,
    register_exception_handlers,
    unhandled_exception_handler,
    validation_exception_handler,
)


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


# ---------------------------------------------------------------------------
# lazy package-level re-export
# ---------------------------------------------------------------------------


def test_fastapi_lazily_re_exported_from_package() -> None:
    """``robotsix_http.fastapi`` is reachable via the lazy re-export.

    The submodule is deliberately not imported eagerly at package top level
    (``fastapi`` is an optional dependency), so it must be resolvable through
    the package ``__getattr__`` instead — both as a direct attribute access and
    via ``from robotsix_http import fastapi``.
    """
    import robotsix_http

    assert "fastapi" in dir(robotsix_http)
    assert robotsix_http.fastapi is __import__("robotsix_http.fastapi", fromlist=[""])
    # Unknown attributes fall through to AttributeError (PEP 562).
    assert not hasattr(robotsix_http, "no_such_attribute")


def test_fastapi_lazy_from_import() -> None:
    from robotsix_http import fastapi

    assert callable(fastapi.DomainError)
    assert callable(fastapi.create_health_router)
    assert callable(fastapi.register_exception_handlers)


def _body(response: JSONResponse) -> dict[str, Any]:
    return json.loads(bytes(response.body))


# ---------------------------------------------------------------------------
# error_envelope
# ---------------------------------------------------------------------------


def test_error_envelope_shape() -> None:
    response = error_envelope(400, "some_code", "some detail")
    assert response.status_code == 400
    assert _body(response) == {"error": {"code": "some_code", "detail": "some detail"}}


def test_error_envelope_encodes_complex_detail() -> None:
    response = error_envelope(422, "validation_error", [{"loc": ("body", "name")}])
    body = _body(response)
    assert body["error"]["code"] == "validation_error"
    # Tuple round-trips to a JSON list via jsonable_encoder.
    assert body["error"]["detail"] == [{"loc": ["body", "name"]}]


# ---------------------------------------------------------------------------
# health route factory
# ---------------------------------------------------------------------------


async def test_health_route_default_path() -> None:
    router = create_health_router()
    routes = [r for r in router.routes if getattr(r, "path", None) == "/health"]
    assert routes, "expected a /health route"
    endpoint = routes[0].endpoint  # type: ignore[attr-defined]
    assert await endpoint() == {"status": "ok"}


def test_health_route_custom_path() -> None:
    router = create_health_router("/healthz")
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/healthz" in paths


# ---------------------------------------------------------------------------
# exception handler suite
# ---------------------------------------------------------------------------


async def test_validation_exception_handler() -> None:
    exc = RequestValidationError(
        [{"type": "missing", "loc": ("body", "name"), "msg": "Field required"}]
    )
    response = await validation_exception_handler(_request(), exc)
    assert response.status_code == 422
    body = _body(response)
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["detail"], list)


async def test_http_exception_handler() -> None:
    exc = StarletteHTTPException(status_code=404, detail="not here")
    response = await http_exception_handler(_request(), exc)
    assert response.status_code == 404
    assert _body(response) == {"error": {"code": "http_error", "detail": "not here"}}


async def test_domain_error_handler_custom() -> None:
    exc = DomainError("bad thing", code="bad_thing", status_code=409)
    response = await domain_error_handler(_request(), exc)
    assert response.status_code == 409
    assert _body(response) == {"error": {"code": "bad_thing", "detail": "bad thing"}}


async def test_domain_error_handler_defaults() -> None:
    exc = DomainError("plain")
    response = await domain_error_handler(_request(), exc)
    assert response.status_code == 400
    assert _body(response) == {"error": {"code": "domain_error", "detail": "plain"}}


async def test_external_auth_error() -> None:
    exc = ExternalAuthError("auth failed", status_code=401, response=httpx.Response(401))
    response = await external_http_error_handler(_request(), exc)
    assert response.status_code == 502
    assert _body(response)["error"]["code"] == "upstream_auth_error"


async def test_external_rate_limit_error() -> None:
    exc = ExternalRateLimitError("slow down", status_code=429, response=httpx.Response(429))
    response = await external_http_error_handler(_request(), exc)
    assert response.status_code == 429
    assert _body(response)["error"]["code"] == "upstream_rate_limited"


async def test_external_service_error() -> None:
    exc = ExternalServiceError("upstream down", status_code=503, response=httpx.Response(503))
    response = await external_http_error_handler(_request(), exc)
    assert response.status_code == 502
    assert _body(response)["error"]["code"] == "upstream_service_error"


async def test_external_base_error_falls_through() -> None:
    exc = ExternalHTTPError("weird", status_code=418, response=httpx.Response(418))
    response = await external_http_error_handler(_request(), exc)
    assert response.status_code == 502
    assert _body(response)["error"]["code"] == "upstream_error"


async def test_unhandled_exception_handler() -> None:
    response = await unhandled_exception_handler(_request(), RuntimeError("kaboom"))
    assert response.status_code == 500
    assert _body(response) == {
        "error": {"code": "internal_error", "detail": "Internal Server Error"}
    }


# ---------------------------------------------------------------------------
# register_exception_handlers wiring
# ---------------------------------------------------------------------------


def test_register_exception_handlers_wires_suite() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    handlers = app.exception_handlers
    assert handlers[RequestValidationError] is validation_exception_handler
    assert handlers[StarletteHTTPException] is http_exception_handler
    assert handlers[DomainError] is domain_error_handler
    assert handlers[ExternalHTTPError] is external_http_error_handler
    assert handlers[Exception] is unhandled_exception_handler


# ---------------------------------------------------------------------------
# chat-skill descriptor helpers
# ---------------------------------------------------------------------------

_SKILL = """\
---
name: robotsix-calendar
description: Calendar service exposing CRUD operations over events.
---

# robotsix-calendar

## Safety rules

- `GET /events` — list events (read-only).
- `POST /events` — create an event (confirmation-gated).
- `DELETE /events/{event_id}` — delete an event (confirmation-gated).
"""


def test_parse_chat_skill_frontmatter_valid() -> None:
    frontmatter = parse_chat_skill_frontmatter(_SKILL)
    assert frontmatter == ChatSkillFrontmatter(
        name="robotsix-calendar",
        description="Calendar service exposing CRUD operations over events.",
    )


def test_parse_chat_skill_frontmatter_strips_quotes() -> None:
    text = '---\nname: robotsix-invest\ndescription: "One sentence."\n---\nbody\n'
    frontmatter = parse_chat_skill_frontmatter(text)
    assert frontmatter.description == "One sentence."


def test_parse_chat_skill_frontmatter_missing_block() -> None:
    with pytest.raises(ValueError, match="frontmatter block"):
        parse_chat_skill_frontmatter("# no frontmatter here\n")


def test_parse_chat_skill_frontmatter_bad_name() -> None:
    text = "---\nname: Robotsix_Calendar\ndescription: A thing.\n---\n"
    with pytest.raises(ValueError, match="kebab-case"):
        parse_chat_skill_frontmatter(text)


def test_parse_chat_skill_frontmatter_empty_description() -> None:
    text = "---\nname: robotsix-calendar\ndescription:\n---\n"
    with pytest.raises(ValueError, match="description"):
        parse_chat_skill_frontmatter(text)


async def test_create_chat_skill_router_serves_markdown() -> None:
    router = create_chat_skill_router(_SKILL)
    routes = [r for r in router.routes if getattr(r, "path", None) == "/chat-skill"]
    assert routes, "expected a /chat-skill route"
    endpoint = routes[0].endpoint  # type: ignore[attr-defined]
    response = await endpoint()
    assert response.status_code == 200
    assert response.media_type == "text/markdown"
    assert response.body.decode() == _SKILL


def test_create_chat_skill_router_custom_path() -> None:
    router = create_chat_skill_router(_SKILL, path="/skill")
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/skill" in paths


def test_create_chat_skill_router_validates_eagerly() -> None:
    with pytest.raises(ValueError, match="frontmatter block"):
        create_chat_skill_router("# missing frontmatter\n")


def test_create_chat_skill_router_name_mismatch() -> None:
    with pytest.raises(ValueError, match="component id"):
        create_chat_skill_router(_SKILL, name="robotsix-invest")


def test_create_chat_skill_router_name_match() -> None:
    router = create_chat_skill_router(_SKILL, name="robotsix-calendar")
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/chat-skill" in paths


def _events_app() -> FastAPI:
    app = FastAPI()

    @app.get("/events")
    async def list_events() -> list[dict[str, Any]]:
        return []

    @app.post("/events")
    async def create_event() -> dict[str, Any]:
        return {}

    @app.delete("/events/{event_id}")
    async def delete_event(event_id: str) -> dict[str, Any]:
        return {}

    return app


def test_documented_routes_extracts_method_paths() -> None:
    assert documented_routes(_SKILL) == {
        "/events",
        "/events/{event_id}",
    }


def test_app_route_paths_lists_api_routes() -> None:
    app = _events_app()
    app.include_router(create_health_router())
    assert app_route_paths(app) == {"/events", "/events/{event_id}", "/health"}


def test_assert_chat_skill_route_parity_ok() -> None:
    app = _events_app()
    app.include_router(create_health_router())
    app.include_router(create_chat_skill_router(_SKILL))
    assert_chat_skill_route_parity(app, _SKILL)


def test_assert_chat_skill_route_parity_undocumented_route() -> None:
    app = _events_app()

    @app.get("/secret")
    async def secret() -> dict[str, Any]:
        return {}

    with pytest.raises(AssertionError, match="absent from the chat-skill"):
        assert_chat_skill_route_parity(app, _SKILL)


def test_assert_chat_skill_route_parity_dangling_documented_route() -> None:
    app = FastAPI()

    @app.get("/events")
    async def list_events() -> list[dict[str, Any]]:
        return []

    with pytest.raises(AssertionError, match="documented in the chat-skill"):
        assert_chat_skill_route_parity(app, _SKILL)


def test_assert_chat_skill_route_parity_ignore_extra() -> None:
    app = _events_app()

    @app.get("/metrics")
    async def metrics() -> dict[str, Any]:
        return {}

    assert_chat_skill_route_parity(app, _SKILL, ignore={"/metrics"})
