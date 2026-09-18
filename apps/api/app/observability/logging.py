"""Structured logging via structlog.

All backend code should use `get_logger(__name__)` — never the stdlib
`logging` module directly. The bound context (user_id, request_id,
trace_id) is added automatically by middleware and worker task
wrappers, so log statements stay short.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import Processor

from app.settings import get_settings


def configure_logging() -> None:
    """Idempotent — safe to call at both api and worker startup."""
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    # Route stdlib logs through structlog so third-party libs (uvicorn,
    # sqlalchemy) share the same JSON format in prod.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    if settings.environment == "dev":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
