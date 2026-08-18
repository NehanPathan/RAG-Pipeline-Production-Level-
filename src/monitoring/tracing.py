from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

_configured = False

# The one identifier that stitches the five observability layers together:
# it is bound into structlog's contextvars (so every log line in the request
# carries it), set as an attribute on the root OTel span and the Langfuse
# trace, returned to the client in the `X-Trace-Id` header and the SSE `done`
# event, and persisted on messages.langfuse_trace_id. One value takes you
# from a user complaint to the exact span tree that produced the answer.
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def configure_tracing(service_name: str, otlp_endpoint: str) -> None:
    """Install a real OTel TracerProvider exporting to an OTLP collector.

    Idempotent — safe to call once at app startup. Without calling this,
    get_tracer() still returns a usable tracer that produces no-op
    (non-recording) spans, which is what lets unit tests use traced_stage()
    with zero setup. It also means forgetting to call it silently disables
    every span in the process, so `lifespan()` calls it unconditionally
    whenever an endpoint is configured.
    """
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    _configured = True


def instrument_fastapi(app: Any) -> None:
    """Auto-instrument the ASGI app so every route gets a server span.

    Best-effort: a failure here must not stop the API from booting, since
    losing instrumentation is strictly less bad than losing the service.
    """
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="/api/v1/metrics,/api/v1/health")
    except Exception:  # pragma: no cover - depends on optional extra
        pass


def get_tracer(name: str = "prod_rag") -> Tracer:
    return trace.get_tracer(name)


def set_current_trace_id(trace_id: str) -> None:
    _trace_id_var.set(trace_id)


def get_current_trace_id() -> str:
    """The request's trace id, or the active OTel span's if none was bound.

    Falls back to the OTel span context so background tasks and scripts that
    never pass through the HTTP middleware still correlate, and returns ""
    rather than raising when there is no trace at all.
    """
    bound = _trace_id_var.get()
    if bound:
        return bound
    span = trace.get_current_span()
    context = span.get_span_context()
    if context.is_valid:
        return format(context.trace_id, "032x")
    return ""


def reset_tracing() -> None:
    """Test helper: allow configure_tracing() to install a provider again."""
    global _configured
    _configured = False
    _trace_id_var.set("")
