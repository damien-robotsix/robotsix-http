"""Test the FastAPI service-bootstrap module."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.testclient import TestClient

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
    validation_exception_handler,
)
from robotsix_http.client import (
    ExternalAuthError,
    ExternalRateLimitError,
    ExternalServiceError,
)


def _request() -> Any:
    """Mock request."""
    return type("MockRequest", (), {})()


def _body(response: Any) -> dict[str, Any]:
    """Extract body from a JSONResponse."""
    import json

    return json.loads(response.body.decode())


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


async def test_health_route_with_extra_fields_callback() -> None:
    """Test that extra_fields_fn callback is called and merged into response."""

    def extra_fields() -> dict[str, Any]:
        return {"auth_configured": True, "version": "1.0"}

    router = create_health_router(extra_fields_fn=extra_fields)
    routes = [r for r in router.routes if getattr(r, "path", None) == "/health"]
    assert routes, "expected a /health route"
    endpoint = routes[0].endpoint  # type: ignore[attr-defined]
    result = await endpoint()
    assert result == {"status": "ok", "auth_configured": True, "version": "1.0"}


async def test_health_route_extra_fields_override_base() -> None:
    """Test that extra fields can override the base status field if needed."""

    def extra_fields() -> dict[str, Any]:
        return {"status": "degraded", "details": "cache unavailable"}

    router = create_health_router(extra_fields_fn=extra_fields)
    routes = [r for r in router.routes if getattr(r, "path", None) == "/health"]
    endpoint = routes[0].endpoint  # type: ignore[attr-defined]
    result = await endpoint()
    # extra fields override the base status
    assert result["status"] == "degraded"
    assert result["details"] == "cache unavailable"


async def test_health_route_extra_fields_empty() -> None:
    """Test that empty extra_fields dict is handled gracefully."""

    def extra_fields() -> dict[str, Any]:
        return {}

    router = create_health_router(extra_fields_fn=extra_fields)
    routes = [r for r in router.routes if getattr(r, "path", None) == "/health"]
    endpoint = routes[0].endpoint  # type: ignore[attr-defined]
    result = await endpoint()
    assert result == {"status": "ok"}


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


async def test_domain_error_handler_default() -> None:
    exc = DomainError("bad thing")
    response = await domain_error_handler(_request(), exc)
    assert response.status_code == 400
    assert _body(response) == {"error": {"code": "domain_error", "detail": "bad thing"}}


async def test_external_http_error_handler_auth() -> None:
    exc = ExternalAuthError("oops")
    response = await external_http_error_handler(_request(), exc)
    assert response.status_code == 502
    body = _body(response)
    assert body["error"]["code"] == "upstream_auth_error"


async def test_external_http_error_handler_rate_limit() -> None:
    exc = ExternalRateLimitError("oops")
    response = await external_http_error_handler(_request(), exc)
    assert response.status_code == 429
    body = _body(response)
    assert body["error"]["code"] == "upstream_rate_limited"


async def test_external_http_error_handler_service() -> None:
    exc = ExternalServiceError("oops")
    response = await external_http_error_handler(_request(), exc)
    assert response.status_code == 502
    body = _body(response)
    assert body["error"]["code"] == "upstream_service_error"


# ---------------------------------------------------------------------------
# error envelope
# ---------------------------------------------------------------------------


def test_error_envelope_simple() -> None:
    response = error_envelope(400, "test_error", "test detail")
    assert response.status_code == 400
    body = _body(response)
    assert body["error"]["code"] == "test_error"
    assert body["error"]["detail"] == "test detail"


def test_error_envelope_nested_detail() -> None:
    detail = {"nested": {"error": "value"}}
    response = error_envelope(422, "validation_error", detail)
    assert response.status_code == 422
    body = _body(response)
    assert body["error"]["detail"]["nested"]["error"] == "value"


# ---------------------------------------------------------------------------
# exception handler registration
# ---------------------------------------------------------------------------


def test_register_exception_handlers() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    # Just verify the app has handlers registered.
    # Detailed handler testing is above.
    assert len(app.exception_handlers) > 0


# ---------------------------------------------------------------------------
# chat-skill frontmatter parsing
# ---------------------------------------------------------------------------


def test_parse_chat_skill_frontmatter_valid() -> None:
    markdown = '---\nname: my-component\ndescription: A test component.\n---\n'
    frontmatter = parse_chat_skill_frontmatter(markdown)
    assert frontmatter.name == "my-component"
    assert frontmatter.description == "A test component."


def test_parse_chat_skill_frontmatter_quoted() -> None:
    markdown = '---\nname: "my-component"\ndescription: "A test component."\n---\n'
    frontmatter = parse_chat_skill_frontmatter(markdown)
    assert frontmatter.name == "my-component"
    assert frontmatter.description == "A test component."


def test_parse_chat_skill_frontmatter_with_body() -> None:
    markdown = '---\nname: my-comp\ndescription: A test.\n---\n\nSome body text.'
    frontmatter = parse_chat_skill_frontmatter(markdown)
    assert frontmatter.name == "my-comp"
    assert frontmatter.description == "A test."


def test_parse_chat_skill_frontmatter_invalid_no_block() -> None:
    markdown = "Some text without frontmatter."
    with pytest.raises(ValueError, match="must begin with"):
        parse_chat_skill_frontmatter(markdown)


def test_parse_chat_skill_frontmatter_invalid_name() -> None:
    markdown = '---\nname: Invalid Name\ndescription: A test.\n---\n'
    with pytest.raises(ValueError, match="kebab-case"):
        parse_chat_skill_frontmatter(markdown)


def test_parse_chat_skill_frontmatter_missing_name() -> None:
    markdown = '---\ndescription: A test.\n---\n'
    with pytest.raises(ValueError, match="kebab-case"):
        parse_chat_skill_frontmatter(markdown)


def test_parse_chat_skill_frontmatter_missing_description() -> None:
    markdown = '---\nname: my-comp\n---\n'
    with pytest.raises(ValueError, match="must be a non-empty"):
        parse_chat_skill_frontmatter(markdown)


def test_parse_chat_skill_frontmatter_empty_description() -> None:
    markdown = '---\nname: my-comp\ndescription: \n---\n'
    with pytest.raises(ValueError, match="must be a non-empty"):
        parse_chat_skill_frontmatter(markdown)


# ---------------------------------------------------------------------------
# chat-skill router factory
# ---------------------------------------------------------------------------


def test_create_chat_skill_router() -> None:
    markdown = '---\nname: my-comp\ndescription: A test.\n---\nSome content.'
    router = create_chat_skill_router(markdown)
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/chat-skill" in paths


def test_create_chat_skill_router_custom_path() -> None:
    markdown = '---\nname: my-comp\ndescription: A test.\n---\nSome content.'
    router = create_chat_skill_router(markdown, path="/skill")
    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/skill" in paths


def test_create_chat_skill_router_name_validation() -> None:
    markdown = '---\nname: my-comp\ndescription: A test.\n---\nSome content.'
    # Matching name succeeds
    router = create_chat_skill_router(markdown, name="my-comp")
    assert router

    # Mismatched name fails
    with pytest.raises(ValueError, match="does not match expected"):
        create_chat_skill_router(markdown, name="other-comp")


def test_create_chat_skill_router_invalid_frontmatter() -> None:
    markdown = "No frontmatter here."
    with pytest.raises(ValueError):
        create_chat_skill_router(markdown)


# ---------------------------------------------------------------------------
# route introspection
# ---------------------------------------------------------------------------


def test_documented_routes() -> None:
    markdown = """
---
name: my-api
description: Test API.
---

- GET /users
- POST /users
- GET /users/{id}
- DELETE /users/{id}
"""
    routes = documented_routes(markdown)
    assert "/users" in routes
    assert "/users/{id}" in routes


def test_documented_routes_inline_code() -> None:
    markdown = "Call `GET /events` to list events or `POST /events` to create one."
    routes = documented_routes(markdown)
    assert "/events" in routes


def test_app_route_paths() -> None:
    app = FastAPI()
    app.include_router(create_health_router())
    paths = app_route_paths(app)
    assert "/health" in paths


def test_assert_chat_skill_route_parity_match() -> None:
    markdown = '---\nname: my-comp\ndescription: A test.\n---\nGET /health\n'
    app = FastAPI()
    app.include_router(create_health_router())
    # Should not raise
    assert_chat_skill_route_parity(app, markdown)


def test_assert_chat_skill_route_parity_undocumented() -> None:
    markdown = '---\nname: my-comp\ndescription: A test.\n---\n'
    app = FastAPI()
    app.include_router(create_health_router())
    # /health is in the app but not documented
    with pytest.raises(AssertionError, match="routes registered"):
        assert_chat_skill_route_parity(app, markdown, ignore=[])


def test_assert_chat_skill_route_parity_dangling() -> None:
    markdown = '---\nname: my-comp\ndescription: A test.\n---\nGET /missing\n'
    app = FastAPI()
    # /missing is documented but not in the app
    with pytest.raises(AssertionError, match="dangling"):
        assert_chat_skill_route_parity(app, markdown)


def test_assert_chat_skill_route_parity_ignore() -> None:
    markdown = '---\nname: my-comp\ndescription: A test.\n---\nGET /health\nGET /chat-skill\n'
    app = FastAPI()
    app.include_router(create_health_router())
    # /health and /chat-skill are ignored by default, so this should pass
    assert_chat_skill_route_parity(app, markdown)


# ---------------------------------------------------------------------------
# integration: full app
# ---------------------------------------------------------------------------


def test_full_app_with_exception_handlers() -> None:
    """Test a complete FastAPI app with all standard handlers."""

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(create_health_router())

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_full_app_with_extra_fields() -> None:
    """Test a complete FastAPI app with health route extended by extra_fields_fn."""

    def extra_fields() -> dict[str, Any]:
        return {"ready": True}

    app = FastAPI()
    app.include_router(create_health_router(extra_fields_fn=extra_fields))

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "ready": True}
