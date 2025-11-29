# auth_module.py
# Authentication and authorization utilities (multi-tenant, with permissions)

from contextlib import closing
from typing import Optional, Dict, Any, List
import hashlib

import streamlit as st
import psycopg2
import psycopg2.extras

from db_config import connect

SESSION_USER_KEY = "user"


# ==========================
# Helper: password hashing
# ==========================

def _hash_password(password: str) -> str:
    if password is None:
        return ""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# ==========================
# Schema / init
# ==========================

def init_auth() -> None:
    """
    Ensure auth-related schema is in place.
    We do NOT drop or override existing tables – we only create/alter if missing.
    This is to avoid the UndefinedColumn error for can_create_voucher, etc.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            # Make sure the users table exists (if it already exists, this does nothing)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id                  SERIAL PRIMARY KEY,
                    company_id          INTEGER NOT NULL,
                    username            TEXT NOT NULL,
                    password_hash       TEXT NOT NULL,
                    role                TEXT NOT NULL DEFAULT 'user',
                    can_create_voucher  BOOLEAN NOT NULL DEFAULT FALSE,
                    can_approve_voucher BOOLEAN NOT NULL DEFAULT FALSE,
                    can_manage_users    BOOLEAN NOT NULL DEFAULT FALSE
                );
                """
            )

            # Make sure permission columns exist on older schemas
            cur.execute(
                """
                ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS can_create_voucher  BOOLEAN NOT NULL DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS can_approve_voucher BOOLEAN NOT NULL DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS can_manage_users    BOOLEAN NOT NULL DEFAULT FALSE;
                """
            )

            # Some older schemas might have stored password in a "password" column.
            # We don't remove it, we just ensure it exists so our SELECT can reference it if needed.
            cur.execute(
                """
                ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS password TEXT;
                """
            )

            # Very minimal companies table if not present; we don't touch existing if it already exists.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    id      SERIAL PRIMARY KEY,
                    code    TEXT NOT NULL UNIQUE,
                    name    TEXT NOT NULL
                );
                """
            )

            conn.commit()
    except Exception as e:
        # Don't crash the whole app during init; just warn.
        st.warning(f"init_auth() encountered a non-fatal error: {e}")


# ==========================
# Core user lookup
# ==========================

def verify_user(company_code: str, username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Look up a user by company_code + username + password.
    Returns:
        {
            "id", "username", "role", "company_id",
            "company_name", "company_code",
            "can_create_voucher", "can_approve_voucher", "can_manage_users"
        }
    or None if invalid.
    """
    company_code_norm = (company_code or "").strip().lower()
    username_norm = (username or "").strip().lower()
    if not company_code_norm or not username_norm or not password:
        return None

    pw_hash = _hash_password(password)

    with closing(connect()) as conn, closing(
        conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    ) as cur:
        # Check both password_hash and legacy plain password column (if it exists)
        cur.execute(
            """
            SELECT
                u.id,
                u.username,
                u.role,
                u.company_id,
                COALESCE(c.name, '') AS company_name,
                COALESCE(c.code, '') AS company_code,
                COALESCE(u.can_create_voucher,  FALSE) AS can_create_voucher,
                COALESCE(u.can_approve_voucher, FALSE) AS can_approve_voucher,
                COALESCE(u.can_manage_users,    FALSE) AS can_manage_users
            FROM users u
            LEFT JOIN companies c
              ON u.company_id = c.id
            WHERE LOWER(COALESCE(c.code, '')) = %s
              AND LOWER(u.username)          = %s
              AND (
                    u.password_hash = %s
                 OR u.password      = %s
              )
            LIMIT 1;
            """,
            (company_code_norm, username_norm, pw_hash, password),
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "company_id": row["company_id"],
        "company_name": row["company_name"],
        "company_code": row["company_code"],
        "can_create_voucher": bool(row["can_create_voucher"]),
        "can_approve_voucher": bool(row["can_approve_voucher"]),
        "can_manage_users": bool(row["can_manage_users"]),
    }


def current_user() -> Optional[Dict[str, Any]]:
    return st.session_state.get(SESSION_USER_KEY)


# ==========================
# Guards
# ==========================

def require_login() -> None:
    """
    If not logged in, show a login form.
    On success, sets st.session_state['user'] and reruns the app.
    """
    user = current_user()
    if user:
        return

    st.title("Login")

    with st.form("login_form"):
        company_code = st.text_input("Company Code")
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")

    if not submit:
        st.stop()

    u = verify_user(company_code, username, password)
    if not u:
        st.error("Invalid company code, username or password.")
        st.stop()

    st.session_state[SESSION_USER_KEY] = u
    st.experimental_rerun()


def require_admin() -> None:
    u = current_user()
    if not u:
        require_login()
        return

    if (u.get("role") or "").lower() != "admin":
        st.error("You must be an admin to view this page.")
        st.stop()


def require_permission(permission_name: str) -> None:
    """
    permission_name should be one of:
      - "can_create_voucher"
      - "can_approve_voucher"
      - "can_manage_users"

    Admins are always allowed.
    """
    u = current_user()
    if not u:
        require_login()
        return

    # Admin bypass
    if (u.get("role") or "").lower() == "admin":
        return

    allowed = bool(u.get(permission_name, False))
    if not allowed:
        st.error("You do not have permission to access this feature.")
        st.stop()


# ==========================
# User management helpers
# ==========================

def list_users(company_id: int) -> List[Dict[str, Any]]:
    with closing(connect()) as conn, closing(
        conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    ) as cur:
        cur.execute(
            """
            SELECT
                id,
                username,
                role,
                company_id,
                COALESCE(can_create_voucher,  FALSE) AS can_create_voucher,
                COALESCE(can_approve_voucher, FALSE) AS can_approve_voucher,
                COALESCE(can_manage_users,    FALSE) AS can_manage_users
            FROM users
            WHERE company_id = %s
            ORDER BY username;
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    result: List[Dict[str, Any]] = []
    for r in rows:
        result.append(
            {
                "id": r["id"],
                "username": r["username"],
                "role": r["role"],
                "company_id": r["company_id"],
                "can_create_voucher": bool(r["can_create_voucher"]),
                "can_approve_voucher": bool(r["can_approve_voucher"]),
                "can_manage_users": bool(r["can_manage_users"]),
            }
        )
    return result


def create_user_for_company(
    company_id: int,
    username: str,
    password: str,
    role: str,
    can_create_voucher: bool,
    can_approve_voucher: bool,
    can_manage_users: bool,
    actor_username: str,
) -> Optional[str]:
    """
    Create a new user record.
    Returns an error message string, or None on success.
    """
    username_norm = (username or "").strip()
    if not username_norm:
        return "Username is required."
    if not password:
        return "Password is required."

    pw_hash = _hash_password(password)

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO users (
                    company_id,
                    username,
                    password_hash,
                    role,
                    can_create_voucher,
                    can_approve_voucher,
                    can_manage_users
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    company_id,
                    username_norm,
                    pw_hash,
                    role,
                    can_create_voucher,
                    can_approve_voucher,
                    can_manage_users,
                ),
            )
            conn.commit()
    except psycopg2.Error as e:
        return f"Database error creating user: {e}"
    except Exception as e:
        return f"Error creating user: {e}"

    return None


def update_user_permissions(
    actor_username: str,
    user_id: int,
    company_id: int,
    role: str,
    can_create_voucher: bool,
    can_approve_voucher: bool,
    can_manage_users: bool,
) -> Optional[str]:
    """
    Update a user's role and permission flags.
    Returns an error message string, or None on success.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    role                = %s,
                    can_create_voucher  = %s,
                    can_approve_voucher = %s,
                    can_manage_users    = %s
                WHERE id = %s
                  AND company_id = %s;
                """,
                (
                    role,
                    can_create_voucher,
                    can_approve_voucher,
                    can_manage_users,
                    user_id,
                    company_id,
                ),
            )
            conn.commit()
    except psycopg2.Error as e:
        return f"Database error updating user: {e}"
    except Exception as e:
        return f"Error updating user: {e}"

    return None
