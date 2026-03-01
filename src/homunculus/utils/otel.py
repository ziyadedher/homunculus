"""Structlog processor that injects OpenTelemetry trace context into log entries."""

from opentelemetry import trace
from structlog.types import EventDict, WrappedLogger


def add_otel_context(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Add trace_id and span_id from the current OTel span, if available."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.trace_id:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict
