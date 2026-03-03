"""Structured logging configuration using structlog."""

import logging
import sys

import structlog
from structlog.stdlib import BoundLogger

from homunculus.utils.config import LogFormat
from homunculus.utils.otel import add_otel_context


def get_logger() -> BoundLogger:
    """Return a structlog logger."""
    return structlog.get_logger()


def configure_logging(level: str = "INFO", fmt: LogFormat = "console") -> None:
    """Configure structlog and bridge stdlib logging.

    Args:
        level: Log level name (e.g. "INFO", "DEBUG").
        fmt: Output format — "console" for dev, "json" for prod.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_otel_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging so library logs (httpx, etc.) go through structlog
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *shared_processors,
            renderer,
        ],
    )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Suppress noisy httpx/httpcore request logs (they leak OAuth tokens in URLs)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
