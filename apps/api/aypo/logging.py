"""structlog configuration: human-readable in development, JSON in deployment."""

from __future__ import annotations

import logging
import sys

import structlog

from aypo.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.aypo_log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.dev.ConsoleRenderer()
        if settings.aypo_env == "development"
        else structlog.processors.JSONRenderer()
    )
    # httpx logs a line per request at INFO. That is noise in a run log, and
    # historically it is also where credentials in query strings leak.
    for noisy in ("httpx", "httpcore", "openai._base_client", "anthropic._base_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
