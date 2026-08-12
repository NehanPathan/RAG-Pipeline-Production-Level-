"""Drop-in replacement for `httpx` that attaches the Firebase token.

The UI pages were each written against `httpx` directly
(`httpx.get(url, timeout=5)`), so retro-fitting auth meant either rewriting
every call site or making the module they call behave correctly. This is the
latter:

    import httpx                      ->   from src.ui import http as httpx

Every existing `httpx.get/post/put/delete(...)` in that page then carries the
`Authorization: Bearer <idToken>` header, with no other edit. Explicit
`headers=` still wins, so a call that needs something different is unaffected.

The point is that adding auth cannot be *forgotten* on one page: there is no
un-authed path left to call.
"""

from __future__ import annotations

from typing import Any

import httpx as _httpx

from src.ui.auth import auth_headers

# Re-exported so `except httpx.HTTPError` in existing page code keeps working
# after the import swap.
HTTPError = _httpx.HTTPError
RequestError = _httpx.RequestError
TimeoutException = _httpx.TimeoutException
Response = _httpx.Response

_DEFAULT_TIMEOUT = 15


def _merge(kwargs: dict) -> dict:
    """Inject auth headers without clobbering explicit ones."""
    headers = dict(auth_headers())
    headers.update(kwargs.pop("headers", None) or {})
    kwargs["headers"] = headers
    kwargs.setdefault("timeout", _DEFAULT_TIMEOUT)
    return kwargs


def get(url: str, **kwargs: Any) -> _httpx.Response:
    return _httpx.get(url, **_merge(kwargs))


def post(url: str, **kwargs: Any) -> _httpx.Response:
    return _httpx.post(url, **_merge(kwargs))


def put(url: str, **kwargs: Any) -> _httpx.Response:
    return _httpx.put(url, **_merge(kwargs))


def patch(url: str, **kwargs: Any) -> _httpx.Response:
    return _httpx.patch(url, **_merge(kwargs))


def delete(url: str, **kwargs: Any) -> _httpx.Response:
    return _httpx.delete(url, **_merge(kwargs))


def stream(method: str, url: str, **kwargs: Any):
    return _httpx.stream(method, url, **_merge(kwargs))


def Client(**kwargs: Any) -> _httpx.Client:  # noqa: N802 - mirrors httpx.Client
    return _httpx.Client(**_merge(kwargs))
