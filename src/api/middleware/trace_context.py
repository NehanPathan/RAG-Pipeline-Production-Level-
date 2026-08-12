from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from src.monitoring.logger import get_logger
from src.monitoring.tracing import get_tracer, set_current_trace_id

logger = get_logger(__name__)

TRACE_HEADER = "X-Trace-Id"


class TraceContextMiddleware(BaseHTTPMiddleware):
    """Establishes the one identifier that correlates every observability layer.

    On each request this binds a trace id into four places at once:

      1. structlog contextvars — `merge_contextvars` is already the first
         processor in configure_logging(), so *every* log line emitted
         downstream picks it up with no call-site changes.
      2. A root OTel span — which every `traced_stage()` span then nests
         under, so the whole pipeline appears as one tree instead of a dozen
         orphans.
      3. A module-level ContextVar — readable from non-HTTP code such as
         `governance.audit.record()`, which has no access to the request.
      4. The `X-Trace-Id` response header — so a user reporting a bad answer
         can quote the id rather than an approximate timestamp.

    An inbound `X-Trace-Id` is honoured so a trace started by an upstream
    gateway or the Streamlit UI continues rather than restarting here.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = _sanitise(request.headers.get(TRACE_HEADER)) or uuid.uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            path=request.url.path,
            method=request.method,
        )
        set_current_trace_id(trace_id)
        request.state.trace_id = trace_id

        with get_tracer().start_as_current_span("http_request") as span:
            span.set_attribute("trace_id", trace_id)
            span.set_attribute("http.route", request.url.path)
            span.set_attribute("http.method", request.method)
            try:
                response = await call_next(request)
            except Exception:
                # Log before re-raising: the exception handler runs outside
                # this middleware's contextvars scope, so this is the last
                # point at which the failure is still attached to its trace.
                logger.exception("request_failed")
                structlog.contextvars.clear_contextvars()
                raise
            span.set_attribute("http.status_code", response.status_code)

        response.headers[TRACE_HEADER] = trace_id
        structlog.contextvars.clear_contextvars()
        return response


def _sanitise(raw: str | None) -> str:
    """Accept only a plausible trace id from an untrusted header.

    The value is echoed into logs and a response header, so it is bounded to
    hex characters and 64 chars to keep an attacker from injecting
    newlines into the log stream or oversized headers into the response.
    """
    if not raw:
        return ""
    candidate = raw.strip()
    if len(candidate) > 64 or not all(c in "0123456789abcdefABCDEF-" for c in candidate):
        return ""
    return candidate
