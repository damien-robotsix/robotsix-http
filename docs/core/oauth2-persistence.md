# OAuth2 token persistence

Several robotsix services (e.g. `robotsix-linkedin`, `robotsix-auto-mail`) persist OAuth2 credentials to disk with the same security posture: a `0600` secret file inside a `0700` parent directory, wrapped in a refresh-and-repersist lifecycle behind a zero-argument token-provider callable. The `robotsix_http.oauth` module extracts the provider-agnostic primitives so each consumer can layer its provider-specific serialization on top.

## Secure file I/O

Read and write an opaque secret string with strict Unix permissions:

```python
from robotsix_http import write_secret_file, read_secret_file

# Write a secret to disk: creates parent (0700) and file (0600)
# If parent already exists with loose permissions, they are tightened.
write_secret_file("/path/to/cache", "bearer-token-secret")

# Read it back; returns None on missing or unreadable file (graceful cold-start)
token = read_secret_file("/path/to/cache")

# Pass None or "" to disable persistence
write_secret_file(None, "secret")  # no-op
read_secret_file("")  # returns None
```

## Secure token store

Combine file I/O with caller-supplied serialization (JSON, MSAL `SerializableTokenCache`, etc.):

```python
import json
from robotsix_http import SecureTokenStore

# Define how to serialize and deserialize your token
store = SecureTokenStore(
    path="/path/to/cache.json",
    dumps=json.dumps,
    loads=json.loads,
)

# Save a token
token_dict = {"access_token": "abc123", "expires_at": 1234567890}
store.save(token_dict)

# Load it back; returns None if missing, corrupt, or disabled
loaded = store.load()

# Check if persistence is enabled
if store.enabled:
    print("Persistence is active")
```

The store tolerates a corrupt on-disk payload: if `loads()` raises, the error is logged at debug level and `None` is returned, allowing a graceful degrade to a cold start instead of crashing.

## Refresh-and-repersist lifecycle

Manage the token validity and refresh cycle in one place:

```python
from robotsix_http import refresh_and_persist

# Acquire a valid token, refreshing and re-persisting if needed
token = refresh_and_persist(
    load=store.load,  # Load current token
    is_valid=lambda t: t.get("expires_at") > time.time(),  # Check validity
    refresh=lambda current: oauth_client.refresh(current),  # Refresh provider
    persist=store.save,  # Re-persist the refreshed token
)
```

- If a cached token exists and `is_valid()` accepts it, it is returned unchanged.
- If none exists (cold start) or `is_valid()` rejects it, `refresh()` is called with the current token (or `None` on cold start), and the result is persisted and returned.

## Token-provider factory

Build a zero-argument `() -> str` access-token provider for downstream HTTP clients:

```python
from robotsix_http import build_token_provider


def acquire():
    """Closure over the store and refresh logic."""
    return refresh_and_persist(
        load=store.load,
        is_valid=lambda t: t.get("expires_at") > time.time(),
        refresh=refresh_oauth_token,
        persist=store.save,
    )


provider = build_token_provider(
    acquire=acquire,
    extract=lambda token: token["access_token"],
)

# Pass it to an HTTP client
rc = RetryClient(client, auth=("Bearer", provider))
# or
headers = {"Authorization": f"Bearer {provider()}"}
```

Each call to `provider()` invokes `acquire()` to obtain a fresh (or cached and valid) token and extracts the bearer secret.

## Full example

Compose all primitives for a complete OAuth2 token-persistence workflow:

```python
import json
import time
from robotsix_http import (
    SecureTokenStore,
    build_token_provider,
    refresh_and_persist,
    RetryClient,
)


# Define your provider (e.g. OAuth2 code-flow client)
class OAuth2Client:
    def refresh(self, current_token):
        # Call upstream OAuth2 provider to refresh
        return {"access_token": "new-token", "expires_at": time.time() + 3600}


oauth_client = OAuth2Client()

# Set up the secure token store
store = SecureTokenStore(
    path="~/.cache/myservice/oauth2.json",
    dumps=json.dumps,
    loads=json.loads,
)


# Build the token provider
def acquire():
    return refresh_and_persist(
        load=store.load,
        is_valid=lambda t: t.get("expires_at", 0) > time.time(),
        refresh=oauth_client.refresh,
        persist=store.save,
    )


provider = build_token_provider(
    acquire=acquire,
    extract=lambda t: t["access_token"],
)

# Use it in HTTP requests
async with httpx.AsyncClient() as client:
    rc = RetryClient(client)
    resp = await rc.get(
        "https://api.linkedin.com/v2/me",
        headers={"Authorization": f"Bearer {provider()}"},
    )
```
