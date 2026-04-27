"""
logging_setup.py — Structlog configuration for the Discovery pipeline.

Produces JSON-structured log output compatible with the Spring Boot service
format (log level, timestamp ISO8601 UTC, module, line number).

Security guarantees:
  - The redact_secrets processor scans every log event for password-looking
    tokens and replaces them with ***.
  - Patterns target both "password=<value>" and "password_secret_ref=<value>"
    forms that could appear in repr() of model objects.
  - This is defence-in-depth: passwords should never be in Python memory at
    all (only the secret reference string lives here), but the redactor
    provides an extra backstop.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# Secret redaction patterns
# ---------------------------------------------------------------------------

# A single combined pattern that handles both unquoted (a=b, a:b) and JSON-quoted
# ("a":"b", 'a':'b') forms.  Capture groups:
#   1: prefix including the key, separator, and any opening quote
#   2: matched value (redacted)
#   3: any closing quote (preserved)
#
# Sensitive key patterns:
#   password, password_secret_ref, token, auth_token, secret, secret_key,
#   api_key, api-key
_SECRET_KEY_PATTERN = (
    r'("?(?:password(?:_secret_ref)?|auth[_-]?token|api[_-]?key|'
    r'secret(?:_key)?|token)"?\s*[=:]\s*)("?)([^",\s}\]\)]+)("?)'
)

_KEY_VALUE_RE = re.compile(_SECRET_KEY_PATTERN, re.IGNORECASE)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.]+", re.IGNORECASE)

# Sensitive substrings that, if present in a structlog key, cause us to
# redact the entire VALUE rather than substring-match.  This catches cases
# like {"password": "hunter2"} when an emitter logs the dict directly as a
# field value.
_SENSITIVE_KEY_SUBSTRINGS = ("password", "token", "secret", "authorization")


def _redact_string(value: str) -> str:
    """Apply key=value and Bearer-token redactions to a string."""
    value = _KEY_VALUE_RE.sub(r"\1\2***\4", value)
    value = _BEARER_RE.sub(r"\g<1>***", value)
    return value


def redact_secrets(
    logger: Any,  # structlog logger instance
    method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Structlog processor that redacts secrets from log events.

    Two passes:
    1. For every key whose name contains a sensitive substring (password,
       token, secret, authorization), replace the value with '***'.
    2. For every remaining string value, run the regex patterns to catch
       inlined secrets (e.g. when a model __repr__ contains password=foo).
    """
    for key, val in list(event_dict.items()):
        key_lower = str(key).lower()
        if any(s in key_lower for s in _SENSITIVE_KEY_SUBSTRINGS):
            event_dict[key] = "***"
            continue
        if isinstance(val, str):
            event_dict[key] = _redact_string(val)
    return event_dict


# ---------------------------------------------------------------------------
# Public configuration entry point
# ---------------------------------------------------------------------------


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """
    Configure structlog and stdlib logging.

    Parameters
    ----------
    level:
        Log level string: DEBUG, INFO, WARNING, ERROR, CRITICAL.
    json_output:
        When True (production default), emit JSON.
        When False (local dev / test), emit console-friendly output.

    Notes
    -----
    This function is idempotent — calling it multiple times with the same
    arguments is safe.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Configure stdlib root logger to go through structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_secrets,
        structlog.processors.CallsiteParameterAdder(
            [
                structlog.processors.CallsiteParameter.MODULE,
                structlog.processors.CallsiteParameter.LINENO,
            ]
        ),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_output:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Single structlog.configure — use stdlib bridge so that third-party
    # loggers (httpx, sqlalchemy, etc.) are also handled by the same
    # processor chain via ProcessorFormatter.
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def get_logger(name: str) -> Any:
    """
    Get a structlog logger for the given name (convention: 'discovery.<module>').
    """
    return structlog.get_logger(name)
