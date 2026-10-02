"""Chat-skill descriptor subsystem: frontmatter parsing and route-parity.

The robotsix chat-access standard mandates an opt-in ``GET /chat-skill``
endpoint that serves a ``text/markdown`` descriptor whose YAML frontmatter
declares a kebab-case ``name`` (the component id) and a one-sentence
``description``.  The helpers below let every FastAPI service serve and
validate that descriptor — and keep it in sync with real routes — instead of
hand-rolling the serving, the frontmatter and the route-parity test.

This module is imported and its public names re-exported by
:mod:`robotsix_http.fastapi`, which remains the single public import surface.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import PlainTextResponse

__all__ = [
    "ChatSkillFrontmatter",
    "app_route_paths",
    "assert_chat_skill_route_parity",
    "create_chat_skill_router",
    "documented_routes",
    "parse_chat_skill_frontmatter",
]

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_KEBAB_CASE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_DOCUMENTED_ROUTE_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s`)>\"']*)")
_DEFAULT_PARITY_IGNORE = frozenset({"/health", "/chat-skill"})


@dataclass(frozen=True)
class ChatSkillFrontmatter:
    """Validated YAML frontmatter of a chat-skill descriptor.

    :param name: the kebab-case component id.
    :param description: the one-sentence component description.
    """

    name: str
    description: str


def _unquote(value: str) -> str:
    """Strip a single pair of matching surrounding quotes from ``value``."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_chat_skill_frontmatter(markdown_text: str) -> ChatSkillFrontmatter:
    """Parse and validate the YAML frontmatter of a chat-skill descriptor.

    Per the robotsix chat-access standard the descriptor must begin with a
    ``---``-delimited YAML block declaring a kebab-case ``name`` and a
    non-empty one-sentence ``description``.  Only those two scalar fields are
    required; any others are ignored.

    :raises ValueError: if the frontmatter block is missing, the ``name`` is
        absent or not kebab-case, or the ``description`` is absent or empty.
    """
    match = _FRONTMATTER_RE.match(markdown_text)
    if match is None:
        raise ValueError(
            "chat-skill descriptor must begin with a '---'-delimited YAML frontmatter block"
        )

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        fields[key.strip()] = _unquote(value.strip())

    name = fields.get("name", "")
    description = fields.get("description", "")
    if not _KEBAB_CASE_RE.fullmatch(name):
        raise ValueError(f"chat-skill frontmatter 'name' must be kebab-case; got {name!r}")
    if not description:
        raise ValueError(
            "chat-skill frontmatter 'description' must be a non-empty one-sentence string"
        )
    return ChatSkillFrontmatter(name=name, description=description)


def create_chat_skill_router(
    markdown_text: str,
    *,
    path: str = "/chat-skill",
    name: str | None = None,
) -> APIRouter:
    """Return an :class:`~fastapi.APIRouter` serving a chat-skill descriptor.

    The route responds to ``GET {path}`` with HTTP 200 and the descriptor as
    ``text/markdown`` — mirroring :func:`create_health_router`.

    The descriptor's frontmatter is validated eagerly (see
    :func:`parse_chat_skill_frontmatter`) so a malformed descriptor fails at
    router-construction time rather than on first request.  Pass ``name`` to
    additionally assert the frontmatter ``name`` matches the expected
    component id.

    :raises ValueError: if the frontmatter is invalid or ``name`` is given and
        does not match the frontmatter ``name``.
    """
    frontmatter = parse_chat_skill_frontmatter(markdown_text)
    if name is not None and frontmatter.name != name:
        raise ValueError(
            f"chat-skill frontmatter 'name' {frontmatter.name!r} does not "
            f"match expected component id {name!r}"
        )

    router = APIRouter()

    @router.get(path, response_class=PlainTextResponse)
    async def chat_skill() -> PlainTextResponse:
        return PlainTextResponse(markdown_text, media_type="text/markdown")

    return router


def app_route_paths(app: FastAPI) -> set[str]:
    """Return the set of HTTP route paths exposed by ``app``.

    Uses ``app.openapi()`` so included sub-routers are resolved and
    framework-mounted routes (``/openapi.json``, ``/docs``, ``/redoc``) are
    excluded automatically.  Routes hidden from the schema
    (``include_in_schema=False``) are not returned.
    """
    paths: dict[str, Any] = app.openapi().get("paths", {})
    return set(paths)


def documented_routes(markdown_text: str) -> set[str]:
    """Return the set of route paths documented in a chat-skill descriptor.

    A route is considered documented when its path follows an HTTP method verb
    (e.g. ``GET /events`` or ``POST /events/{event_id}``) anywhere in the
    descriptor body — the shape the chat-access standard uses to classify
    read-only vs confirmation-gated operations.
    """
    paths: set[str] = set()
    for raw in _DOCUMENTED_ROUTE_RE.findall(markdown_text):
        path = raw.rstrip("`.,;:)")
        if path:
            paths.add(path)
    return paths


def assert_chat_skill_route_parity(
    app: FastAPI,
    markdown_text: str,
    *,
    ignore: Iterable[str] = (),
) -> None:
    """Assert the chat-skill descriptor and ``app`` describe the same routes.

    Gives every service calendar's guarantee for free: every route registered
    on ``app`` is documented in the descriptor, and every route documented in
    the descriptor resolves to a registered route.  ``/health`` and
    ``/chat-skill`` are ignored by default; pass ``ignore`` to skip additional
    infrastructure paths.

    Intended to be called from a pytest test — combine with
    :func:`app_route_paths` and :func:`documented_routes` when you want to
    parametrize over individual routes instead.

    :raises AssertionError: if any registered route is undocumented or any
        documented route does not resolve.
    """
    ignored = _DEFAULT_PARITY_IGNORE | frozenset(ignore)
    registered = app_route_paths(app) - ignored
    documented = documented_routes(markdown_text) - ignored

    undocumented = sorted(registered - documented)
    if undocumented:
        raise AssertionError(
            "routes registered on the app but absent from the chat-skill "
            f"descriptor: {undocumented}"
        )

    dangling = sorted(documented - registered)
    if dangling:
        raise AssertionError(
            "routes documented in the chat-skill descriptor that do not "
            f"resolve to a registered app route: {dangling}"
        )
