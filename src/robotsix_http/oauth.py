"""Provider-neutral secure OAuth2 token-persistence scaffolding.

Several robotsix services persist OAuth2 credentials to disk with the same
security posture — a ``0600`` secret file inside a ``0700`` parent directory —
and wrap it in the same refresh-and-repersist lifecycle behind a zero-argument
token-provider callable.  That scaffolding was hand-rolled verbatim (modulo the
provider's own serialization) in ``robotsix-linkedin``
(``linkedin_service.auth.TokenStore``) and ``robotsix-auto-mail``
(``robotsix_auto_mail.oauth2``).

This module extracts the *provider-agnostic* pieces so each consumer can layer
its provider-specific serialization on top:

* **Secure persistence primitives** — :func:`write_secret_file` /
  :func:`read_secret_file` write and read an opaque secret string with a
  ``0600`` mode inside a ``0700`` parent.  An empty/``None`` path disables
  persistence (write is a no-op, read returns ``None``); a missing or
  unreadable file reads back as ``None`` rather than raising.
* **A secure token store** — :class:`SecureTokenStore` combines the primitives
  with caller-supplied ``dumps``/``loads`` callables and additionally tolerates
  a *corrupt* on-disk payload (a ``loads`` failure) by returning ``None``.
* **A refresh-and-repersist lifecycle** — :func:`refresh_and_persist` returns a
  still-valid cached token or refreshes and re-persists it.
* **A token-provider factory** — :func:`build_token_provider` wraps a token
  accessor into the zero-argument ``() -> str`` callable that downstream HTTP
  clients expect.

The module is deliberately provider-neutral: it never imports a provider SDK
and knows nothing about token shape, scopes, or grant type.  Consumers keep
their own serialization (JSON dict, MSAL ``SerializableTokenCache``, ...),
validity checks, and refresh flows.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: A filesystem path accepted by the persistence helpers.  ``None`` or an empty
#: value disables persistence.
StrPath = str | os.PathLike[str]

__all__ = [
    "SecureTokenStore",
    "StrPath",
    "build_token_provider",
    "read_secret_file",
    "refresh_and_persist",
    "write_secret_file",
]


def write_secret_file(path: StrPath | None, data: str, *, encoding: str = "utf-8") -> None:
    """Write *data* to *path* as a ``0600`` file inside a ``0700`` parent.

    An empty or ``None`` *path* disables persistence and the call is a no-op,
    so a consumer can transparently run without an on-disk cache.

    The parent directory is created (``0700``) if absent and its mode is
    tightened even when it already existed with looser permissions.  The file
    is created with ``O_CREAT | O_TRUNC`` and mode ``0o600``; because the
    ``mode`` argument to :func:`os.open` is ignored for a pre-existing file,
    the mode is re-applied with :func:`os.chmod` afterwards so an
    already-present file is hardened too.
    """
    if not path:
        return
    target = Path(path)
    parent = target.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        parent.chmod(0o700)
    except OSError:
        logger.debug("could not tighten permissions on %s", parent, exc_info=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding=encoding) as handle:
        handle.write(data)
    os.chmod(target, 0o600)


def read_secret_file(path: StrPath | None, *, encoding: str = "utf-8") -> str | None:
    """Return the text content of *path*, or ``None`` when unavailable.

    An empty or ``None`` *path* (persistence disabled), a missing file, or any
    other read error yields ``None`` rather than raising — a missing cache is a
    normal cold-start condition, not an error.
    """
    if not path:
        return None
    target = Path(path)
    try:
        return target.read_text(encoding=encoding)
    except FileNotFoundError:
        return None
    except OSError:
        logger.debug("could not read secret file %s", target, exc_info=True)
        return None


@dataclass
class SecureTokenStore[TokenT]:
    """A secure on-disk cache for a provider-defined token value.

    Combines the secure persistence primitives with caller-supplied
    serialization so the store stays provider-neutral:

    * *path* — where to persist; ``None``/empty disables persistence entirely
      (:meth:`load` returns ``None``, :meth:`save` is a no-op).
    * *dumps* — serialize the token to the string written to disk.
    * *loads* — deserialize the on-disk string back into a token.

    On top of the missing-file tolerance of :func:`read_secret_file`,
    :meth:`load` also tolerates a *corrupt* payload: if *loads* raises, the
    error is logged at debug level and ``None`` is returned so a damaged cache
    degrades to a cold start instead of crashing the consumer.
    """

    path: StrPath | None
    dumps: Callable[[TokenT], str]
    loads: Callable[[str], TokenT]

    @property
    def enabled(self) -> bool:
        """Return ``True`` when a non-empty path is configured."""
        return bool(self.path)

    def load(self) -> TokenT | None:
        """Return the cached token, or ``None`` if absent/corrupt/disabled."""
        raw = read_secret_file(self.path)
        if raw is None:
            return None
        try:
            return self.loads(raw)
        except Exception:
            logger.debug("ignoring corrupt secret file %s", self.path, exc_info=True)
            return None

    def save(self, token: TokenT) -> None:
        """Persist *token* securely; a no-op when persistence is disabled."""
        if not self.path:
            return
        write_secret_file(self.path, self.dumps(token))


def refresh_and_persist[TokenT](
    *,
    load: Callable[[], TokenT | None],
    is_valid: Callable[[TokenT], bool],
    refresh: Callable[[TokenT | None], TokenT],
    persist: Callable[[TokenT], None],
) -> TokenT:
    """Return a valid token, refreshing and re-persisting it when necessary.

    Loads the current token via *load*; if one exists and *is_valid* accepts
    it, it is returned unchanged.  Otherwise *refresh* is called with the
    current token (or ``None`` on a cold start) to obtain a fresh one, the
    result is handed to *persist*, and the refreshed token is returned.
    """
    current = load()
    if current is not None and is_valid(current):
        return current
    refreshed = refresh(current)
    persist(refreshed)
    return refreshed


def build_token_provider[TokenT](
    *,
    acquire: Callable[[], TokenT],
    extract: Callable[[TokenT], str],
) -> Callable[[], str]:
    """Build a zero-argument ``() -> str`` access-token provider.

    Each call invokes *acquire* (typically a closure over
    :func:`refresh_and_persist`) to obtain a valid token and *extract* to pull
    the bearer secret out of it.  The returned callable is the shape downstream
    HTTP clients expect for an ``Authorization`` header source.
    """

    def _provider() -> str:
        return extract(acquire())

    return _provider
