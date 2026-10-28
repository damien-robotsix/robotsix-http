"""Tests for the liveness / startup / readiness probes of create_health_router."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx
from fastapi import FastAPI

from robotsix_http.fastapi import HealthCheck, ProbeState, create_health_router


def _client(**kwargs: Any) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(create_health_router(**kwargs))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _get(path: str, **kwargs: Any) -> httpx.Response:
    async with _client(**kwargs) as client:
        return await client.get(path)


async def _ok() -> None:
    return None


async def _boom() -> None:
    raise RuntimeError("db down")


async def _bare_error() -> None:
    raise RuntimeError


async def _slow() -> None:
    await asyncio.sleep(10)


def _paths(**kwargs: Any) -> set[str]:
    router = create_health_router(**kwargs)
    return {getattr(r, "path", "") for r in router.routes}


# ---------------------------------------------------------------------------
# backwards compatibility
# ---------------------------------------------------------------------------


async def test_zero_argument_mounts_only_plain_health() -> None:
    assert _paths() == {"/health"}
    resp = await _get("/health")
    assert resp.status_code == 200
    assert resp.content == b'{"status":"ok"}'


async def test_probe_routes_mounted_when_state_given() -> None:
    assert _paths(state=ProbeState()) == {"/health", "/health/live", "/health/ready"}
    assert _paths(state=ProbeState(), startup_path="/health/startup", live_path=None) == {
        "/health",
        "/health/ready",
        "/health/startup",
    }


async def test_plain_health_unaffected_by_state() -> None:
    state = ProbeState()
    state.mark_shutting_down()
    resp = await _get("/health", state=state)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# state transitions
# ---------------------------------------------------------------------------


def test_probe_state_transitions() -> None:
    state = ProbeState()
    assert not state.started
    assert not state.shutting_down
    state.mark_started()
    assert state.started
    state.mark_shutting_down()
    assert state.shutting_down


async def test_liveness_ok_until_shutting_down_and_ignores_checks() -> None:
    state = ProbeState()  # not started: liveness does not care
    checks: Mapping[str, HealthCheck] = {"db": _boom}
    resp = await _get("/health/live", state=state, checks=checks)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    state.mark_shutting_down()
    resp = await _get("/health/live", state=state, checks=checks)
    assert resp.status_code == 503
    assert resp.json() == {"status": "shutting_down"}


async def test_startup_probe_until_mark_started() -> None:
    state = ProbeState()
    resp = await _get("/startup", state=state, startup_path="/startup")
    assert resp.status_code == 503
    assert resp.json() == {"status": "starting"}

    state.mark_started()
    resp = await _get("/startup", state=state, startup_path="/startup")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_follows_state() -> None:
    state = ProbeState()
    resp = await _get("/health/ready", state=state)
    assert resp.status_code == 503
    assert resp.json() == {"status": "starting"}

    state.mark_started()
    resp = await _get("/health/ready", state=state)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    state.mark_shutting_down()
    resp = await _get("/health/ready", state=state)
    assert resp.status_code == 503
    assert resp.json() == {"status": "shutting_down"}


async def test_readiness_does_not_run_checks_while_starting() -> None:
    calls: list[str] = []

    async def check() -> None:
        calls.append("db")

    resp = await _get("/health/ready", state=ProbeState(), checks={"db": check})
    assert resp.status_code == 503
    assert calls == []


# ---------------------------------------------------------------------------
# dependency checks
# ---------------------------------------------------------------------------


async def test_readiness_checks_without_state_are_started() -> None:
    resp = await _get("/health/ready", checks={"db": _ok, "storage": _ok})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_aggregates_failures() -> None:
    resp = await _get(
        "/health/ready",
        checks={"db": _boom, "vault": _ok, "storage": _bare_error},
    )
    assert resp.status_code == 503
    assert resp.json() == {
        "status": "unready",
        "failures": ["db: db down", "storage: RuntimeError"],
    }


async def test_readiness_check_timeout() -> None:
    resp = await _get(
        "/health/ready",
        checks={"slow": _slow, "db": _ok},
        check_timeout_s=0.05,
    )
    assert resp.status_code == 503
    assert resp.json() == {
        "status": "unready",
        "failures": ["slow: timed out after 0.05s"],
    }


async def test_readiness_checks_run_concurrently() -> None:
    async def sleepy() -> None:
        await asyncio.sleep(0.2)

    loop = asyncio.get_running_loop()
    start = loop.time()
    resp = await _get(
        "/health/ready",
        checks={"a": sleepy, "b": sleepy, "c": sleepy},
        check_timeout_s=1.0,
    )
    elapsed = loop.time() - start
    assert resp.status_code == 200
    assert elapsed < 0.5
