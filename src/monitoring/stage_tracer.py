from __future__ import annotations

import time
from types import TracebackType
from typing import Any

from opentelemetry.trace import Status, StatusCode

from src.monitoring.langfuse_tracer import get_langfuse_client
from src.monitoring.logger import get_logger
from src.monitoring.tracing import get_tracer

logger = get_logger(__name__)

_SCALAR_TYPES = (str, int, float, bool)


class TracedStage:
    """Opens one OTel span and one Langfuse span for a single pipeline stage.

    Every Phase 3 module (query intelligence, retrieval, fusion, reranking,
    context processing, semantic cache, answer generation) wraps its work in
    this so each stage is independently visible in both an OTel-compatible
    backend and Langfuse, without each module hand-rolling tracing calls.

    Usage:
        async with traced_stage("vector_search", query=query, top_k=20) as stage:
            results = await vector_repo.search(query_vector, top_k=20)
            stage.set_result(chunks_found=len(results))
    """

    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self._name = name
        self._attributes = {k: v for k, v in attributes.items() if v is not None}
        self._result: dict[str, Any] = {}
        self._start = 0.0

    async def __aenter__(self) -> TracedStage:
        self._otel_cm = get_tracer().start_as_current_span(self._name)
        self._otel_span = self._otel_cm.__enter__()
        for key, value in self._attributes.items():
            if isinstance(value, _SCALAR_TYPES):
                self._otel_span.set_attribute(key, value)

        self._langfuse_cm = get_langfuse_client().start_as_current_observation(
            name=self._name, as_type="span", input=self._attributes or None
        )
        self._langfuse_span = self._langfuse_cm.__enter__()

        self._start = time.perf_counter()
        logger.info(f"{self._name}_start", **self._attributes)
        return self

    def set_result(self, **fields: Any) -> None:
        self._result.update(fields)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        duration_ms = int((time.perf_counter() - self._start) * 1000)
        self._result["duration_ms"] = duration_ms

        if exc is not None:
            self._otel_span.record_exception(exc)
            self._otel_span.set_status(Status(StatusCode.ERROR, str(exc)))
            self._langfuse_span.update(
                level="ERROR", status_message=str(exc), output=self._result
            )
            logger.error(f"{self._name}_failed", error=str(exc), **self._result)
        else:
            for key, value in self._result.items():
                if isinstance(value, _SCALAR_TYPES):
                    self._otel_span.set_attribute(key, value)
            self._langfuse_span.update(output=self._result)
            logger.info(f"{self._name}_done", **self._result)

        self._langfuse_cm.__exit__(exc_type, exc, tb)
        self._otel_cm.__exit__(exc_type, exc, tb)
        return False


def traced_stage(name: str, **attributes: Any) -> TracedStage:
    return TracedStage(name, attributes)
