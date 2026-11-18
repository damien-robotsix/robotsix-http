"""Shared FastAPI config-endpoint factory (versioned, rollback-capable).

Many robotsix services hand-roll the same config endpoint family against a
local backing store:

* ``GET  {path}`` -- return the current config;
* ``PUT  {path}`` -- replace the config (recording a version snapshot);
* ``GET  {versions_path}`` -- list the stored config versions;
* ``POST {rollback_path}`` -- roll the config back to a stored version.

:func:`create_config_router` provides all four routes against a caller-supplied
:class:`ConfigStore`, mirroring the :func:`create_health_router` factory style.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field


class ConfigVersionNotFoundError(Exception):
    """Raised by a ConfigStore when a rollback targets an unknown version.

    :func:`create_config_router` translates this into a 404 response; it is the
    store's signal that the requested version id does not exist in its history.
    """


@dataclass(frozen=True)
class ConfigVersion:
    """A single snapshot in the store's config version history.

    Attributes:
        id: Stable identifier for the version (e.g. ``v7`` or a uuid), used as
            the ``version_id`` in a rollback request.
        created_at: ISO-8601 timestamp of when the snapshot was recorded.
        description: Optional human-readable note describing the change.
        author: Optional identifier of the actor who made the change.
    """

    id: str
    created_at: str
    description: str = ""
    author: str = ""


class ConfigStore(Protocol):
    """The pluggable backing store a :func:`create_config_router` router reads
    and writes.

    Implementations own where the config and its version history live (a file,
    a database, an in-memory dict, ...).  The methods are intentionally
    synchronous so any transport can be wrapped; callers that need async IO
    should implement the store over their own executor/async client.

    :meth:`rollback` should raise :class:`ConfigVersionNotFoundError` when
    *version_id* does not name a known snapshot.
    """

    def get(self) -> dict[str, Any]:
        """Return the current config as a JSON-serialisable dict."""
        pass

    def put(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """Replace the config and record a new version snapshot.

        Returns the newly stored config as a JSON-serialisable dict.
        """
        pass

    def versions(self) -> Sequence[ConfigVersion]:
        """Return the config version history, newest last (or first)."""
        pass

    def rollback(self, version_id: str) -> dict[str, Any]:
        """Roll the config back to *version_id* and return the restored config.

        :raises ConfigVersionNotFoundError: if *version_id* is unknown.
        """
        pass


class _RollbackRequest(BaseModel):
    """Request body for ``POST {rollback_path}``."""

    version_id: str = Field(..., description="Config version id to roll back to.")


def create_config_router(
    store: ConfigStore,
    *,
    path: str = "/config",
    versions_path: str = "/config/versions",
    rollback_path: str = "/config/rollback",
) -> APIRouter:
    """Return an APIRouter serving the config endpoint family.

    Mirrors :func:`create_health_router`: a single factory that mounts the
    routes every robotsix service repeats by hand, backed by a caller-supplied
    :class:`ConfigStore`.

    Routes mounted:

    * ``GET {path}`` -- returns the current config (``store.get()``).
    * ``PUT {path}`` -- accepts the full replacement config as the JSON body,
      persists it (``store.put``) and returns the stored config.
    * ``GET {versions_path}`` -- returns ``{"versions": [...]}`` from
      ``store.versions()``.
    * ``POST {rollback_path}`` -- body ``{"version_id": "..."}``; restores that
      version (``store.rollback``) and returns the restored config.  An unknown
      ``version_id`` (``ConfigVersionNotFoundError``) responds 404.

    The paths are keyword-only and default to the standard ``/config`` family;
    pass alternatives to mount the routes elsewhere.
    """
    router = APIRouter()

    @router.get(path)
    async def get_config() -> dict[str, Any]:
        return store.get()

    @router.put(path)
    async def update_config(
        payload: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        return store.put(payload)

    @router.get(versions_path)
    async def list_versions() -> dict[str, Any]:
        return {"versions": store.versions()}

    @router.post(rollback_path)
    async def rollback(payload: _RollbackRequest) -> dict[str, Any]:
        try:
            return store.rollback(payload.version_id)
        except ConfigVersionNotFoundError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown config version {payload.version_id!r}",
            ) from None

    return router
