"""Tests for credential-storage suppression (password / password_hash).

A column named ``password_hash`` was getting tagged as both ``API_KEY``
(its high-entropy hash matched the entropy regex) AND ``SOX`` (the
inherited regulation tag from API_KEY).  Neither fits credential
storage; both surface the wrong message to the user.

These tests pin down:

  * ``is_credential_name`` recognises the common spellings.
  * The CREDENTIAL_HASH PII type / column-name prior fires for those
    columns — the chip becomes "[Credential Hash]" instead of
    "[SOX] [API Key]".
  * The CREDENTIAL_HASH pattern carries an EMPTY ``regulated`` tuple
    so no misleading regulatory chip is rendered.
"""

from __future__ import annotations

import pytest

from discovery.pii_priors import (
    is_credential_name,
    is_structural_pointer_name,
    name_prior,
    name_prior_strength,
)
from discovery.pii_patterns import get_pattern


# --------------------------------------------------------------------- #
# is_credential_name
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("col", [
    "password",
    "passwd",
    "pwd",
    "password_hash",
    "passwd_hash",
    "pwd_hash",
    "hashed_password",
    "password_digest",
    "user_password",
    "user_passwd",
    "secret_hash",
    "credential_hash",
    "credentials_hash",
    "auth_hash",
    "PASSWORD",          # case-insensitive
    "Password_Hash",
    # Salt + iteration columns live alongside the hash in PBKDF2 /
    # scrypt schemes; they were originally missed and ended up tagged
    # PHONE_US (their value digits matched the phone regex).
    "password_salt",
    "passwd_salt",
    "pwd_salt",
    "password_iterations",
    "pbkdf2_iterations",
    "kdf_iterations",
])
def test_credential_name_matches(col):
    assert is_credential_name(col) is True


@pytest.mark.parametrize("col", [
    "password_changed_at",   # not the hash — timestamp
    "password_reset_token",  # different concept (token, not hash)
    "card_number",           # PCI, unrelated
    "user_id",               # structural pointer
    "first_name",            # PII but not credential
    "api_key",               # also high-entropy but ISN'T a credential hash
])
def test_credential_name_does_not_match_unrelated(col):
    """Token / reset-time / card / id / name / api_key columns are all
    non-credential and must NOT trip the suppression.  api_key is the
    deliberate carve-out: a real API_KEY column SHOULD still surface."""
    assert is_credential_name(col) is False


# --------------------------------------------------------------------- #
# CREDENTIAL_HASH prior
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("col", [
    "password",
    "password_hash",
    "pwd_hash",
    "hashed_password",
])
def test_password_columns_get_credential_hash_prior(col):
    assert name_prior(col) == "CREDENTIAL_HASH"
    assert name_prior_strength(col, "CREDENTIAL_HASH") > 0.0


# --------------------------------------------------------------------- #
# CREDENTIAL_HASH PatternDef carries no regulation tags
# --------------------------------------------------------------------- #


def test_credential_hash_pattern_has_no_regulation_chip():
    """No PCI / SOX / GDPR misclassification — credential storage is
    informational only; the empty regulated tuple ensures the UI
    renders just the [Credential Hash] chip without an additional
    regulation badge."""
    p = get_pattern("CREDENTIAL_HASH")
    assert p is not None, "CREDENTIAL_HASH pattern not registered"
    assert p.regulated == (), (
        f"CREDENTIAL_HASH should carry no regulation tags, "
        f"got {p.regulated}"
    )


# --------------------------------------------------------------------- #
# Structural-pointer interaction (defence in depth)
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("col", [
    "user_password_id",      # contains both 'password' and '_id' — should NOT
                             # be credential because the suffix is a pointer
                             # shape; falls through to structural-pointer.
])
def test_password_pointer_shape_falls_through(col):
    # Implementation detail: credential check is independent; this test
    # documents the boundary.  ``user_password_id`` is recognised by the
    # structural-pointer test (it ends in _id).
    assert is_structural_pointer_name(col) is True
