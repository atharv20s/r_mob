"""
Structured JSON Logging Configuration
======================================
Replaces print() statements with structured JSON logs ready for
CloudWatch / Grafana Loki / ELK ingestion.

Usage:
    from src.core.logging_config import setup_logging
    setup_logging()  # call once at startup

    import logging
    logger = logging.getLogger("my_module")
    logger.info("Request processed", extra={"user_id": 1, "latency_ms": 42})
"""

import logging
import sys
import json
from datetime import datetime, UTC
from typing import Any


class StructuredJSONFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects.

    Output example:
        {"timestamp": "2025-07-16T08:30:00Z", "level": "INFO",
         "logger": "ai.ollama", "message": "Request processed",
         "user_id": 1, "latency_ms": 42}
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge extra fields from the log record
        # (passed via logger.info("msg", extra={...}))
        skip_fields = {
            "name", "msg", "args", "created", "relativeCreated",
            "thread", "threadName", "msecs", "filename", "funcName",
            "lineno", "module", "exc_info", "exc_text", "stack_info",
            "levelname", "levelno", "pathname", "processName", "process",
            "message", "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in skip_fields and not key.startswith("_"):
                log_entry[key] = value

        # Include exception info if present
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def setup_logging(level: str = "INFO", json_format: bool = True) -> None:
    """
    Configure the root logger with structured JSON output.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: If True, use JSON formatter. If False, use human-readable.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicate output
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Stream handler → stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))

    if json_format:
        handler.setFormatter(StructuredJSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  [%(levelname)-7s] %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        ))

    root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
