"""
test_redaction.py — Unit tests for logging_setup.redact_secrets.

These tests call the processor as a plain function (no structlog config) so
the autouse caplog_structlog fixture in conftest.py does not interfere.
"""
from __future__ import annotations

import json

from discovery.logging_setup import _redact_string, redact_secrets


# ---------------------------------------------------------------------------
# String-level redaction
# ---------------------------------------------------------------------------


def test_redact_unquoted_password() -> None:
    out = _redact_string("password=hunter2")
    assert "hunter2" not in out
    assert "password=" in out
    assert "***" in out


def test_redact_json_quoted_password() -> None:
    """JSON-rendered logs put the value in quotes — must still redact."""
    raw = json.dumps({"password": "hunter2"})
    assert raw == '{"password": "hunter2"}'
    out = _redact_string(raw)
    assert "hunter2" not in out, f"hunter2 still visible in: {out!r}"


def test_redact_json_compact_password() -> None:
    raw = '{"password":"hunter2"}'
    out = _redact_string(raw)
    assert "hunter2" not in out, f"hunter2 still visible in: {out!r}"


def test_redact_token_with_colon() -> None:
    out = _redact_string("token: foo")
    assert "foo" not in out
    assert "token" in out


def test_redact_password_secret_ref() -> None:
    out = _redact_string("password_secret_ref=env://MY_VAR")
    assert "MY_VAR" not in out


def test_redact_bearer_authorization() -> None:
    out = _redact_string("Authorization: Bearer abcdef-0123456789")
    assert "abcdef" not in out
    assert "Bearer" in out


def test_redact_api_key() -> None:
    out = _redact_string('"api_key":"sk_live_xyz"')
    assert "sk_live_xyz" not in out


# ---------------------------------------------------------------------------
# Event-dict-level redaction
# ---------------------------------------------------------------------------


def test_event_dict_password_field_redacted() -> None:
    ev = {"event": "msg", "password": "hunter2"}
    out = redact_secrets(None, "info", dict(ev))
    assert out["password"] == "***"


def test_event_dict_auth_token_field_redacted() -> None:
    ev = {"event": "msg", "auth_token": "tok123"}
    out = redact_secrets(None, "info", dict(ev))
    assert out["auth_token"] == "***"


def test_event_dict_event_message_redacted() -> None:
    """The 'event' field with embedded JSON must redact inline secrets."""
    ev = {"event": '{"password":"hunter2"}'}
    out = redact_secrets(None, "info", dict(ev))
    assert "hunter2" not in out["event"]


def test_event_dict_non_sensitive_passthrough() -> None:
    ev = {"event": "ok", "user": "alice"}
    out = redact_secrets(None, "info", dict(ev))
    assert out["user"] == "alice"
    assert out["event"] == "ok"


def test_event_dict_authorization_field_redacted() -> None:
    ev = {"event": "request", "authorization": "Bearer abc.def.ghi"}
    out = redact_secrets(None, "info", dict(ev))
    # 'authorization' contains 'authorization' → entire value blanked.
    assert out["authorization"] == "***"
