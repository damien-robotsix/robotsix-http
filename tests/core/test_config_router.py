"""Tests for robotsix_http._config (config endpoint factory).

The factory routes are exercised through a real FastAPI app via
``httpx.ASGITransport`` (matching the app-level tests in test_fastapi.py) so
the full request/response pipeline, body parsing and error handling are
covered.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from robotsix_http.fastapi import (
    ConfigVersion,
    ConfigVersionNotFoundError,
    create_config_router,
)


class FakeStore:
    """In-memory :class:`~robotsix_http.fastapi.ConfigStore` for tests."""

    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = dict(initial or {})
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._versions: list[ConfigVersion] = []

    def get(self) -> dict[str, Any]:
        return dict(self._config)

    def put(self, config: Mapping[str, Any]) -> dict[str, Any]:
        new_id = f"v{len(self._versions) + 1}"
        self._snapshots[new_id] = dict(config)
        self._versions.append(
            ConfigVersion(id=new_id, created_at=f"2026-01-01T00:00:0{len(self._versions)}Z")
        )
        self._config = dict(config)
        return dict(config)

    def versions(self) -> Sequence[ConfigVersion]:
        return list(self._versions)

    def rollback(self, version_id: str) -> dict[str, Any]:
        if version_id not in self._snapshots:
            raise ConfigVersionNotFoundError(version_id)
        self._config = dict(self._snapshots[version_id])
        return dict(self._config)


def _app(store: FakeStore) -> FastAPI:
    app = FastAPI()
    app.include_router(create_config_router(store))
    return app


async def _get(app: FastAPI, url: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def _put(app: FastAPI, url: str, body: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.put(url, json=body)


async def _post(app: FastAPI, url: str, body: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(url, json=body)


# ---------------------------------------------------------------------------
# route mount / default paths
# ---------------------------------------------------------------------------


def test_config_router_mounts_default_routes() -> None:
    router = create_config_router(FakeStore({"a": 1}))
    paths = {getattr(r, "path", None) for r in router.routes}
    assert paths == {"/config", "/config/versions", "/config/rollback"}


def test_config_router_custom_paths() -> None:
    router = create_config_router(
        FakeStore(),
        path="/settings",
        versions_path="/settings/history",
        rollback_path="/settings/revert",
    )
    paths = {getattr(r, "path", None) for r in router.routes}
    assert paths == {"/settings", "/settings/history", "/settings/revert"}


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


async def test_get_config_returns_current() -> None:
    app = _app(FakeStore({"theme": "dark", "retries": 3}))
    response = await _get(app, "/config")
    assert response.status_code == 200
    assert response.json() == {"theme": "dark", "retries": 3}


# ---------------------------------------------------------------------------
# PUT /config
# ---------------------------------------------------------------------------


async def test_put_config_updates_and_returns_stored() -> None:
    store = FakeStore({"theme": "dark"})
    app = _app(store)
    response = await _put(app, "/config", {"theme": "light", "retries": 5})
    assert response.status_code == 200
    assert response.json() == {"theme": "light", "retries": 5}
    assert store.get() == {"theme": "light", "retries": 5}


async def test_put_config_records_a_version() -> None:
    store = FakeStore({"theme": "dark"})
    app = _app(store)
    await _put(app, "/config", {"theme": "light"})
    versions = store.versions()
    assert len(versions) == 1
    assert versions[0].id == "v1"


# ---------------------------------------------------------------------------
# GET /config/versions
# ---------------------------------------------------------------------------


async def test_get_versions_lists_history() -> None:
    store = FakeStore({"theme": "dark"})
    store.put({"theme": "light"})
    store.put({"theme": "sepia"})
    app = _app(store)
    response = await _get(app, "/config/versions")
    assert response.status_code == 200
    body = response.json()
    assert [v["id"] for v in body["versions"]] == ["v1", "v2"]


# ---------------------------------------------------------------------------
# POST /config/rollback
# ---------------------------------------------------------------------------


async def test_rollback_restores_version() -> None:
    store = FakeStore({"theme": "dark"})
    store.put({"theme": "light"})
    store.put({"theme": "sepia"})
    app = _app(store)
    response = await _post(app, "/config/rollback", {"version_id": "v1"})
    assert response.status_code == 200
    assert response.json() == {"theme": "light"}
    assert store.get() == {"theme": "light"}


async def test_rollback_unknown_version_is_404() -> None:
    app = _app(FakeStore({"theme": "dark"}))
    response = await _post(app, "/config/rollback", {"version_id": "nope"})
    assert response.status_code == 404
    assert "nope" in response.text


async def test_rollback_missing_version_id_is_422() -> None:
    app = _app(FakeStore({"theme": "dark"}))
    response = await _post(app, "/config/rollback", {})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# unknown-version handling is a store exception contract
# ---------------------------------------------------------------------------


def test_store_rollback_raises_not_found() -> None:
    store = FakeStore({"theme": "dark"})
    with pytest.raises(ConfigVersionNotFoundError):
        store.rollback("missing")
