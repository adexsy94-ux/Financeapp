# auth_module.py
# Multi-tenant authentication, company registration, and user management.

import hashlib
from contextlib import closing
from typing import Optional, Dict, List

import psycopg2
import streamlit as st

from db_config import (
    connect,
    COMPANIES_TABLE_SQL,
    AUTH_TABLE_SQL,
    log_action,
)


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# ------------------------
# INTERNAL: ensure user columns exist
# ------------------------

def _ensure_user_columns(cur) -> None:
    """
    Hardened migration to guarantee new columns on existing DBs.
    Safe to run multiple times (errors are ignored).
    """
    alter_statements = (
        "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user';",
        "ALTER TABLE users ADD COLUMN can_create_voucher BOOLEAN DEFAULT TRUE;",
        "ALTER TABLE users ADD COLUMN can_approve_voucher BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE users ADD COLUMN can_manage_users BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE users ADD COLUMN company_id INTEGER;",
    )
    for alter_sql in alter_statements:
        try:
            cur.execute(alter_sql)
        except Exception:
            # Column may already exist, or table definition differs; ignore.
            pass


# ------------------------
# Init helper for app_main
# ------------------------

def init_auth():
    """
    Ensure auth tables and required columns exist.
    This now also ensures role / permission columns are present on users.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            # Base tables (as defined in db_config)
            cur.execute(COMPANIES_TABLE_SQL)
            cur.execute(AUTH_TABLE_SQL)

            # Ensure newer permission columns exist on existing DBs
            _ensure_user_columns(cur)

            conn.commit()
    except Exception:
        # We don't want init to crash the app; any real issues will surface later.
        pass


# ------------------------
# Company + first admin
# ------------------------

def create_company_and_admin(
    company_name: str,
    company_code: str,
    admin_username: str,
    admin_password: str,
) -> Optional[str]:
    """
    Create a new company and its first admin user.
    Returns None on success or error message on failure.
    """
    company_name = (company_name or "").strip()
    company_code_norm = (company_code or "").strip().lower()
    admin_username_norm = (admin_username or "").strip().lower()

    if not company_name or not company_code_norm:
        return "Company name and company code are required."
    if not admin_username_norm or not admin_password:
        return "Admin username and password are required."

    pw_hash = _hash_password(admin_password)

    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        # Ensure tables exist
        cur.execute(COMPANIES_TABLE_SQL)
        cur.execute(AUTH_TABLE_SQL)

        # Ensure required columns exist (same migration logic as init_auth)
        _ensure_user_columns(cur)

        # Check company code uniqueness
        cur.execute(
            "SELECT id FROM companies WHERE lower(code) = %s",
            (company_code_norm,),
        )
        existing = cur.fetchone()
        if existing:
            return "A company with this code already exists."

        # Create company
        cur.execute(
            """
            INSERT INTO companies (name, code)
            VALUES (%s, %s)
            RETURNING id
            """,
            (company_name, company_code_norm),
        )
        company_id = cur.fetchone()["id"]

        # Create admin user
        try:
            cur.execute(
                """
                INSERT INTO users (
                    username,
                    password_hash,
                    company_id,
                    role,
                    can_create_voucher,
                    can_approve_voucher,
                    can_manage_users
                )
                VALUES (%s, %s, %s, %s, TRUE, TRUE, TRUE)
                """,
                (
                    admin_username_norm,
                    pw_hash,
                    company_id,
                    "admin",
                ),
            )
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return "A user with this username already exists. Please choose a different admin username."
        except Exception as ex:
            conn.rollback()
            return f"Error creating admin user: {ex}"

        conn.commit()

    log_action(
        admin_username_norm,
        "create_company_and_admin",
        "companies",
        ref=str(company_id),
        details=f"company_code={company_code_norm}",
        company_id=company_id,
    )

    return None


# ------------------------
# Login & session
# ------------------------

def verify_user(company_code: str, username: str, password: str) -> Optional[Dict]:
    """
    Verify that a username/password exists for a given company code.
    Returns a dict with user & company info on success, or None.
    """
    company_code_norm = (company_code or "").strip().lower()
    username_norm = (username or "").strip().lower()

    if not company_code_norm or not username_norm or not password:
        return None

    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                u.id,
                u.username,
                u.password_hash,
                u.role,
                u.can_create_voucher,
                u.can_approve_voucher,
                u.can_manage_users,
                u.company_id,
                c.name AS company_name,
                c.code AS company_code
            FROM users u
            JOIN companies c ON u.company_id = c.id
            WHERE lower(c.code) = %s
              AND lower(u.username) = %s
            """,
            (company_code_norm, username_norm),
        )
        row = cur.fetchone()

    if not row:
        return None

    if row["password_hash"] != _hash_password(password):
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


def current_user() -> Optional[Dict]:
    return st.session_state.get("user")


def require_login():
    """
    If no user in session, render login + register UI and stop.
    This version does NOT use tabs, so the register-company form is always visible.
    """
    if current_user() is not None:
        return

    st.markdown("<h2>FinanceApp Login</h2>", unsafe_allow_html=True)

    # ========== LOGIN FORM ==========
    st.markdown("### Login")

    with st.form("login_form"):
        company_code = st.text_input("Company Code", key="login_company_code")
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        login_submitted = st.form_submit_button("Login")

    if login_submitted:
        u = verify_user(company_code, username, password)
        if not u:
            st.error("Invalid company code, username or password.")
        else:
            st.session_state["user"] = u
            st.success("Login successful.")
            st.rerun()

    st.markdown("---")

    # ========== REGISTER COMPANY + FIRST ADMIN ==========
    st.markdown("### Register a New Company")

    st.info("Register a new company and create the first admin user.")

    with st.form("register_company_form"):
        company_name = st.text_input("Company Name")
        company_code = st.text_input("Company Code (e.g., ZETACOMS, JAGA)")
        admin_username = st.text_input("Admin Username")
        pw1 = st.text_input("Admin Password", type="password")
        pw2 = st.text_input("Confirm Password", type="password")
        register_submitted = st.form_submit_button("Register Company")

    if register_submitted:
        if not company_name or not company_code or not admin_username or not pw1 or not pw2:
            st.error("All fields are required.")
        elif pw1 != pw2:
            st.error("Passwords do not match.")
        else:
            err = create_company_and_admin(company_name, company_code, admin_username, pw1)
            if err:
                st.error(err)
            else:
                st.success("Company registered. Use the Login form above to sign in.")

    # Prevent the rest of the app from running until logged in
    st.stop()



def require_admin():
    user = current_user()
    if not user:
        require_login()
        return
    if user["role"] != "admin":
        st.error("You do not have permission to view this page.")
        st.stop()


def require_permission(permission_flag: str):
    user = current_user()
    if not user:
        require_login()
        return
    if not user.get(permission_flag, False):
        st.error("You do not have permission for this action.")
        st.stop()


# ------------------------
# User management APIs
# ------------------------

def list_users(company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, username, role,
                   can_create_voucher,
                   can_approve_voucher,
                   can_manage_users,
                   created_at
            FROM users
            WHERE company_id = %s
            ORDER BY username
            """,
            (company_id,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


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
    username_norm = (username or "").strip().lower()
    if not username_norm or not password:
        return "Username and password required."

    if role not in ("user", "admin"):
        return "Invalid role."

    pw_hash = _hash_password(password)

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO users (
                    username,
                    password_hash,
                    company_id,
                    role,
                    can_create_voucher,
                    can_approve_voucher,
                    can_manage_users
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    username_norm,
                    pw_hash,
                    company_id,
                    role,
                    bool(can_create_voucher),
                    bool(can_approve_voucher),
                    bool(can_manage_users),
                ),
            )
            conn.commit()

        log_action(
            actor_username,
            "create_user",
            "users",
            ref=username_norm,
            details=f"company_id={company_id}, role={role}",
            company_id=company_id,
        )
        return None
    except Exception as e:
        return f"Error creating user: {e}"


def update_user_permissions(
    actor_username: str,
    user_id: int,
    company_id: int,
    role: str,
    can_create_voucher: bool,
    can_approve_voucher: bool,
    can_manage_users: bool,
) -> Optional[str]:
    if role not in ("user", "admin"):
        return "Invalid role."

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE users
                SET role = %s,
                    can_create_voucher = %s,
                    can_approve_voucher = %s,
                    can_manage_users = %s
                WHERE id = %s
                  AND company_id = %s
                """,
                (
                    role,
                    bool(can_create_voucher),
                    bool(can_approve_voucher),
                    bool(can_manage_users),
                    user_id,
                    company_id,
                ),
            )
            conn.commit()

        log_action(
            actor_username,
            "update_user_permissions",
            "users",
            ref=str(user_id),
            details=f"company_id={company_id}, role={role}",
            company_id=company_id,
        )
        return None
    except Exception as e:
        return f"Error updating permissions: {e}"
