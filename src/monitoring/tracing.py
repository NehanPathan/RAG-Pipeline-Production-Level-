from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

_configured = False


def configure_tracing(service_name: str, otlp_endpoint: str) -> None:
    """Install a real OTel TracerProvider exporting to an OTLP collector.

    Idempotent — safe to call once at app startup. Without calling this,
    get_tracer() still returns a usable tracer that produces no-op
    (non-recording) spans, which is what lets unit tests use traced_stage()
    with zero setup.
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


def get_tracer(name: str = "prod_rag") -> Tracer:
    return trace.get_tracer(name)


def reset_tracing() -> None:
    """Test helper: allow configure_tracing() to install a provider again."""
    global _configured
    _configured = False
