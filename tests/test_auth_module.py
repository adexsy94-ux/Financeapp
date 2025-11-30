import datetime

import pytest

from auth_module import (
    hash_password,
    verify_password,
    _is_account_locked,
    _utcnow,
)


def test_hash_and_verify_password_roundtrip():
    password = "MySecurePassword123!"
    hashed = hash_password(password)

    assert isinstance(hashed, str)
    assert len(hashed) > 0

    # Correct password passes
    assert verify_password(password, hashed) is True

    # Wrong password fails
    assert verify_password("wrong-password", hashed) is False


def test_is_account_locked_false_when_no_lock():
    now = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

    user_row = {
        "is_active": True,
        "failed_attempts": 0,
        "locked_until": None,
    }

    assert _is_account_locked(user_row, now=now) is False


def test_is_account_locked_true_when_locked_until_future():
    now = datetime.datetime(2025, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    future = datetime.datetime(2025, 1, 1, 12, 30, tzinfo=datetime.timezone.utc)

    user_row = {
        "is_active": True,
        "failed_attempts": 5,
        "locked_until": future,
    }

    assert _is_account_locked(user_row, now=now) is True


def test_is_account_locked_false_when_locked_until_past():
    past = datetime.datetime(2024, 12, 31, 23, 59, tzinfo=datetime.timezone.utc)
    now = datetime.datetime(2025, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)

    user_row = {
        "is_active": True,
        "failed_attempts": 5,
        "locked_until": past,
    }

    assert _is_account_locked(user_row, now=now) is False


def test_is_account_locked_true_when_inactive_user():
    now = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

    user_row = {
        "is_active": False,
        "failed_attempts": 0,
        "locked_until": None,
    }

    assert _is_account_locked(user_row, now=now) is True
