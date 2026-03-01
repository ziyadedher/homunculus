"""OpenTelemetry tracing configuration."""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from homunculus.utils.config import TracingConfig


def configure_tracing(config: TracingConfig) -> None:
    """Set up the OpenTelemetry TracerProvider.

    No-ops if ``config.enabled`` is False.
    """
    if not config.enabled:
        return

    resource = Resource.create({"service.name": config.service_name})
    provider = TracerProvider(resource=resource)

    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=config.endpoint)))

    if config.console_export:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer for the given module name."""
    return trace.get_tracer(name)
