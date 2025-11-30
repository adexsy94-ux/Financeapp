import os
import datetime
from typing import Optional, Dict, Any, Tuple

import bcrypt
from psycopg2.extras import DictRow

from db_config import get_db_cursor

# =========================
# CONFIGURABLE CONSTANTS
# =========================

# Maximum number of failed attempts before locking the account
MAX_FAILED_ATTEMPTS = 5

# Lockout duration in minutes after too many failed attempts
LOCKOUT_MINUTES = 15

# Default session length in minutes (8 hours)
DEFAULT_SESSION_MINUTES = 8 * 60


# =========================
# HELPER FUNCTIONS
# =========================

def _normalize(value: str) -> str:
    """
    Simple normalization for usernames / codes: strip + lowercase.
    """
    return value.strip().lower()


def hash_password(plain_password: str) -> str:
    """
    Hash a password using bcrypt. The returned string includes the salt.
    """
    if not plain_password:
        raise ValueError("Password cannot be empty.")

    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a bcrypt hash.
    """
    if not plain_password or not hashed_password:
        return False

    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except ValueError:
        # If the stored hash is invalid / corrupted
        return False


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _is_account_locked(user_row: DictRow, now: Optional[datetime.datetime] = None) -> bool:
    """
    Check if an account is currently locked.
    Expects columns: locked_until, failed_attempts, is_active.
    """
    if user_row is None:
        return False

    if not user_row.get("is_active", True):
        return True

    locked_until = user_row.get("locked_until")
    if locked_until is None:
        return False

    if now is None:
        now = _utcnow()

    return locked_until > now


def _update_failed_attempt(user_id: int, reset: bool) -> None:
    """
    Update failed_attempts and locked_until for a user.
    - If reset is True, reset counters after a successful login.
    - If reset is False, increment failed_attempts and lock if above threshold.
    """
    now = _utcnow()
    with get_db_cursor() as (conn, cur):
        if reset:
            cur.execute(
                """
                UPDATE users
                SET failed_attempts = 0,
                    locked_until = NULL
                WHERE id = %s
                """,
                (user_id,),
            )
            return

        # Increment failed attempts and fetch the new value
        cur.execute(
            """
            UPDATE users
            SET failed_attempts = failed_attempts + 1
            WHERE id = %s
            RETURNING failed_attempts
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return

        failed_attempts = row[0]
        if failed_attempts >= MAX_FAILED_ATTEMPTS:
            lockout_until = now + datetime.timedelta(minutes=LOCKOUT_MINUTES)
            cur.execute(
                """
                UPDATE users
                SET locked_until = %s
                WHERE id = %s
                """,
                (lockout_until, user_id),
            )


# =========================
# PUBLIC AUTH API
# =========================

def create_company_and_admin(
    company_name: str,
    company_code: str,
    admin_username: str,
    admin_password: str,
) -> Optional[str]:
    """
    Create a new company (if it doesn't exist) and an admin user for that company.
    Returns None on success, or an error message string on failure.

    Expected DB structure (adjust to your current schema if needed):

    - companies:
        id SERIAL PRIMARY KEY
        name TEXT NOT NULL
        code TEXT NOT NULL UNIQUE

    - users:
        id SERIAL PRIMARY KEY
        company_id INTEGER NOT NULL REFERENCES companies(id)
        username TEXT NOT NULL
        password_hash TEXT NOT NULL
        full_name TEXT
        role TEXT NOT NULL DEFAULT 'user'
        is_admin BOOLEAN NOT NULL DEFAULT FALSE
        is_active BOOLEAN NOT NULL DEFAULT TRUE
        failed_attempts INTEGER NOT NULL DEFAULT 0
        locked_until TIMESTAMPTZ
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """
    company_code_norm = _normalize(company_code)
    admin_username_norm = _normalize(admin_username)

    if not admin_password:
        return "Admin password cannot be empty."

    try:
        with get_db_cursor() as (conn, cur):
            # 1. Get or create company
            cur.execute(
                """
                SELECT id FROM companies
                WHERE lower(code) = %s
                """,
                (company_code_norm,),
            )
            row = cur.fetchone()

            if row:
                company_id = row[0]
            else:
                cur.execute(
                    """
                    INSERT INTO companies (name, code)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (company_name.strip(), company_code_norm),
                )
                company_id = cur.fetchone()[0]

            # 2. Check if admin user already exists for this company
            cur.execute(
                """
                SELECT id
                FROM users
                WHERE company_id = %s AND lower(username) = %s
                """,
                (company_id, admin_username_norm),
            )
            existing_user = cur.fetchone()
            if existing_user:
                return "Admin user already exists for this company."

            # 3. Create admin user with bcrypt-hashed password
            password_hash = hash_password(admin_password)

            cur.execute(
                """
                INSERT INTO users (
                    company_id,
                    username,
                    password_hash,
                    full_name,
                    role,
                    is_admin,
                    is_active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    company_id,
                    admin_username_norm,
                    password_hash,
                    "Administrator",
                    "admin",
                    True,
                    True,
                ),
            )

        return None

    except Exception as ex:
        # In a real app, log this error properly
        return f"Error creating company/admin: {ex}"


def verify_user(
    company_code: str,
    username: str,
    password: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Verify a user login attempt.

    Returns (user_dict, error_message):

    - If login is successful:
        (user_dict, None)
    - If login fails:
        (None, "some error message")

    user_dict will include at least:
    - id
    - company_id
    - username
    - full_name
    - role
    - is_admin
    """
    company_code_norm = _normalize(company_code)
    username_norm = _normalize(username)

    if not password:
        return None, "Password cannot be empty."

    with get_db_cursor() as (conn, cur):
        # 1. Get company id
        cur.execute(
            """
            SELECT id
            FROM companies
            WHERE lower(code) = %s
            """,
            (company_code_norm,),
        )
        company_row = cur.fetchone()
        if not company_row:
            return None, "Invalid company code."

        company_id = company_row[0]

        # 2. Fetch user row (including auth-related fields)
        cur.execute(
            """
            SELECT
                id,
                company_id,
                username,
                password_hash,
                full_name,
                role,
                is_admin,
                is_active,
                failed_attempts,
                locked_until
            FROM users
            WHERE company_id = %s AND lower(username) = %s
            """,
            (company_id, username_norm),
        )
        user_row = cur.fetchone()

        if not user_row:
            return None, "Invalid username or password."

        # Convert to dict for convenience
        user = {
            "id": user_row["id"],
            "company_id": user_row["company_id"],
            "username": user_row["username"],
            "password_hash": user_row["password_hash"],
            "full_name": user_row["full_name"],
            "role": user_row["role"],
            "is_admin": user_row["is_admin"],
            "is_active": user_row["is_active"],
            "failed_attempts": user_row["failed_attempts"],
            "locked_until": user_row["locked_until"],
        }

        # 3. Check if account is locked
        if _is_account_locked(user):
            return None, "Account is temporarily locked due to too many failed attempts. Please try again later."

        # 4. Verify password
        if not verify_password(password, user["password_hash"]):
            _update_failed_attempt(user["id"], reset=False)
            return None, "Invalid username or password."

        # 5. Successful login → reset failed attempts
        _update_failed_attempt(user["id"], reset=True)

        # 6. Build a safe public user dict (do not include password hash)
        public_user = {
            "id": user["id"],
            "company_id": user["company_id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"],
            "is_admin": user["is_admin"],
        }

        return public_user, None


# =========================
# SIMPLE SESSION TOKENS
# =========================

def create_session(user_id: int, minutes: int = DEFAULT_SESSION_MINUTES) -> str:
    """
    Create a simple session record in a 'user_sessions' table and return the token.

    Expected DB structure (adjust if you already have something):

    CREATE TABLE IF NOT EXISTS user_sessions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        session_token TEXT NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT TRUE
    );
    """
    import secrets

    token = secrets.token_urlsafe(32)
    now = _utcnow()
    expires_at = now + datetime.timedelta(minutes=minutes)

    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            INSERT INTO user_sessions (
                user_id,
                session_token,
                created_at,
                expires_at,
                is_active
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, token, now, expires_at, True),
        )

    return token


def get_session_user(session_token: str) -> Optional[Dict[str, Any]]:
    """
    Validate a session token and return a user dict if valid, else None.
    """
    if not session_token:
        return None

    now = _utcnow()
    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT
                u.id,
                u.company_id,
                u.username,
                u.full_name,
                u.role,
                u.is_admin,
                s.expires_at,
                s.is_active
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.session_token = %s
            """,
            (session_token,),
        )
        row = cur.fetchone()
        if not row:
            return None

        if not row["is_active"]:
            return None

        if row["expires_at"] <= now:
            # Optionally mark expired
            cur.execute(
                """
                UPDATE user_sessions
                SET is_active = FALSE
                WHERE session_token = %s
                """,
                (session_token,),
            )
            return None

        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "username": row["username"],
            "full_name": row["full_name"],
            "role": row["role"],
            "is_admin": row["is_admin"],
        }


def invalidate_session(session_token: str) -> None:
    """
    Mark a session as inactive (logout).
    """
    if not session_token:
        return

    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE user_sessions
            SET is_active = FALSE
            WHERE session_token = %s
            """,
            (session_token,),
        )
