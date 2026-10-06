"""Tests for the provider-neutral secure OAuth2 token-store scaffolding."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from robotsix_http import (
    SecureTokenStore,
    build_token_provider,
    read_secret_file,
    refresh_and_persist,
    write_secret_file,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# write_secret_file / read_secret_file
# ---------------------------------------------------------------------------


def test_write_secret_file_creates_hardened_file_and_parent(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "token.json"
    write_secret_file(target, "s3cret")
    assert target.read_text(encoding="utf-8") == "s3cret"
    assert _mode(target) == 0o600
    assert _mode(target.parent) == 0o700


def test_write_secret_file_rehardens_existing_loose_permissions(tmp_path: Path) -> None:
    parent = tmp_path / "loose"
    parent.mkdir(mode=0o755)
    target = parent / "token"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o644)

    write_secret_file(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert _mode(target) == 0o600
    assert _mode(parent) == 0o700


@pytest.mark.parametrize("path", [None, ""])
def test_write_secret_file_noop_on_empty_path(path: str | None) -> None:
    # Must not raise; nothing to assert beyond the absence of an error.
    write_secret_file(path, "ignored")


def test_read_secret_file_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "token"
    write_secret_file(target, "value")
    assert read_secret_file(target) == "value"


def test_read_secret_file_missing_returns_none(tmp_path: Path) -> None:
    assert read_secret_file(tmp_path / "absent") is None


@pytest.mark.parametrize("path", [None, ""])
def test_read_secret_file_disabled_returns_none(path: str | None) -> None:
    assert read_secret_file(path) is None


def test_read_secret_file_unreadable_returns_none(tmp_path: Path) -> None:
    # A directory cannot be read as text; the OSError is swallowed to None.
    directory = tmp_path / "dir"
    directory.mkdir()
    assert read_secret_file(directory) is None


# ---------------------------------------------------------------------------
# SecureTokenStore
# ---------------------------------------------------------------------------


def _json_store(path: Path | None) -> SecureTokenStore[dict[str, str]]:
    return SecureTokenStore(path=path, dumps=json.dumps, loads=json.loads)


def test_store_save_and_load_round_trip(tmp_path: Path) -> None:
    store = _json_store(tmp_path / "cache.json")
    store.save({"access_token": "abc"})
    assert store.load() == {"access_token": "abc"}
    assert _mode(tmp_path / "cache.json") == 0o600


def test_store_load_missing_returns_none(tmp_path: Path) -> None:
    assert _json_store(tmp_path / "cache.json").load() is None


def test_store_load_corrupt_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "cache.json"
    write_secret_file(target, "{not valid json")
    assert _json_store(target).load() is None


def test_store_disabled_is_noop(tmp_path: Path) -> None:
    store = _json_store(None)
    assert store.enabled is False
    store.save({"access_token": "abc"})  # no-op, must not raise
    assert store.load() is None


def test_store_enabled_flag(tmp_path: Path) -> None:
    assert _json_store(tmp_path / "cache.json").enabled is True


# ---------------------------------------------------------------------------
# refresh_and_persist
# ---------------------------------------------------------------------------


def test_refresh_and_persist_returns_valid_cached_token() -> None:
    persisted: list[str] = []
    result = refresh_and_persist(
        load=lambda: "cached",
        is_valid=lambda _token: True,
        refresh=lambda _current: pytest.fail("refresh must not run for a valid token"),
        persist=persisted.append,
    )
    assert result == "cached"
    assert persisted == []


def test_refresh_and_persist_refreshes_invalid_token() -> None:
    persisted: list[str] = []
    seen_current: list[str | None] = []

    def refresh(current: str | None) -> str:
        seen_current.append(current)
        return "fresh"

    result = refresh_and_persist(
        load=lambda: "stale",
        is_valid=lambda _token: False,
        refresh=refresh,
        persist=persisted.append,
    )
    assert result == "fresh"
    assert persisted == ["fresh"]
    assert seen_current == ["stale"]


def test_refresh_and_persist_cold_start_passes_none() -> None:
    persisted: list[str] = []
    seen_current: list[str | None] = []

    def refresh(current: str | None) -> str:
        seen_current.append(current)
        return "fresh"

    result = refresh_and_persist(
        load=lambda: None,
        is_valid=lambda _token: pytest.fail("is_valid must not run on cold start"),
        refresh=refresh,
        persist=persisted.append,
    )
    assert result == "fresh"
    assert persisted == ["fresh"]
    assert seen_current == [None]


# ---------------------------------------------------------------------------
# build_token_provider
# ---------------------------------------------------------------------------


def test_build_token_provider_extracts_secret_each_call() -> None:
    calls: list[int] = []

    def acquire() -> dict[str, str]:
        calls.append(1)
        return {"access_token": "bearer-value"}

    provider = build_token_provider(acquire=acquire, extract=lambda t: t["access_token"])
    assert provider() == "bearer-value"
    assert provider() == "bearer-value"
    assert len(calls) == 2


def test_build_token_provider_composes_with_store_and_refresh(tmp_path: Path) -> None:
    store = _json_store(tmp_path / "cache.json")
    refresh_calls: list[int] = []

    def refresh(_current: dict[str, str] | None) -> dict[str, str]:
        refresh_calls.append(1)
        return {"access_token": "minted", "valid": "yes"}

    def acquire() -> dict[str, str]:
        return refresh_and_persist(
            load=store.load,
            is_valid=lambda token: token.get("valid") == "yes",
            refresh=refresh,
            persist=store.save,
        )

    provider = build_token_provider(acquire=acquire, extract=lambda t: t["access_token"])

    assert provider() == "minted"  # cold start -> refresh + persist
    assert provider() == "minted"  # cached and valid -> no second refresh
    assert refresh_calls == [1]
    assert store.load() == {"access_token": "minted", "valid": "yes"}
