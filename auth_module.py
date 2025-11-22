# auth_module.py
# Authentication + multi-tenant user management (companies + users)

import hashlib
from contextlib import closing
from typing import Optional, Dict, List

import streamlit as st

from db_config import connect, AUTH_TABLE_SQL, COMPANIES_TABLE_SQL, log_action


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# ------------------------
# Init helper (what app_main imports)
# ------------------------

def init_auth():
    """
    Ensure the users table exists.

    Kept mainly for backward compatibility with app_main imports:
    from auth_module import init_auth, ...
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(AUTH_TABLE_SQL)
            conn.commit()
    except Exception:
        # Don't crash app startup if this fails
        pass


# ------------------------
# Company Lookup
# ------------------------

def get_company_by_code(code: str) -> Optional[Dict]:
    code_norm = (code or "").strip().lower()
    if not code_norm:
        return None

    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, name, code
            FROM companies
            WHERE lower(code) = %s
            """,
            (code_norm,),
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": row["id"],
        "name": row["name"],
        "code": row["code"],
    }


# ------------------------
# Create Company + First Admin
# ------------------------

def create_company_and_admin(
    company_name: str,
    company_code: str,
    admin_username: str,
    admin_password: str,
) -> Optional[str]:
    """
    Create a new company and its first admin user.
    Returns error message or None on success.
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
        # Ensure companies table exists
        cur.execute(COMPANIES_TABLE_SQL)

        # Check if company exists
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

        # Ensure users table exists
        cur.execute(AUTH_TABLE_SQL)

        # Create admin
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

        conn.commit()

    log_action(
        admin_username_norm,
        "create_company_and_admin",
        "companies",
        ref=str(company_id),
        details=f"company_code={company_code_norm}",
    )

    return None


# ------------------------
# Login
# ------------------------

def verify_user(company_code: str, username: str, password: str) -> Optional[Dict]:
    """
    Verify that a username/password exists for a given company code.
    Returns a dict with user + company info on success, None otherwise.
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

    stored_hash = row["password_hash"]
    if stored_hash != _hash_password(password):
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


# ------------------------
# Auth Helpers
# ------------------------

def current_user() -> Optional[Dict]:
    return st.session_state.get("user")


def require_login():
    """
    If no user in session, render login / register UI and stop.
    """
    if current_user() is not None:
        return

    st.markdown("<h2>VoucherPro Login</h2>", unsafe_allow_html=True)

    tab_login, tab_register = st.tabs(["Login", "Register Company"])

    # LOGIN TAB
    with tab_login:
        company_code = st.text_input("Company Code", key="login_company_code")
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")

        if st.button("Login"):
            u = verify_user(company_code, username, password)
            if not u:
                st.error("Invalid company code, username or password.")
            else:
                st.session_state["user"] = u
                st.success("Login successful.")
                st.experimental_rerun()

    # REGISTER COMPANY TAB
    with tab_register:
        st.info("Register a new company and create the first admin.")

        company_name = st.text_input("Company Name")
        company_code = st.text_input("Company Code (e.g., ZETACOMS, JAGA)")
        admin_username = st.text_input("Admin Username")
        pw1 = st.text_input("Admin Password", type="password")
        pw2 = st.text_input("Confirm Password", type="password")

        if st.button("Register Company"):
            if not company_name or not company_code or not admin_username or not pw1 or not pw2:
                st.error("All fields are required.")
            elif pw1 != pw2:
                st.error("Passwords do not match.")
            else:
                err = create_company_and_admin(company_name, company_code, admin_username, pw1)
                if err:
                    st.error(err)
                else:
                    st.success("Company registered. Go to Login tab to sign in.")

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
    """
    Example: require_permission("can_create_voucher"),
             require_permission("can_approve_voucher"),
             require_permission("can_manage_users").
    """
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
    """
    List all users for the given company.
    """
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
    """
    Create a new user inside a company.
    Returns error message or None on success.
    """
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
    """
    Update role + permission flags for a user.
    """
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
            details=f"company_id={company_id}, role={role}, "
                    f"create={can_create_voucher}, approve={can_approve_voucher}, "
                    f"manage_users={can_manage_users}",
        )
        return None

    except Exception as e:
        return f"Error updating permissions: {e}"


def change_password(company_id: int, username: str, old_password: str, new_password: str) -> Optional[str]:
    """
    Change password for a user in a given company.
    """
    username_norm = (username or "").strip().lower()
    if not username_norm or not old_password or not new_password:
        return "All fields required."

    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT u.id, u.password_hash
            FROM users u
            WHERE u.company_id = %s
              AND lower(u.username) = %s
            """,
            (company_id, username_norm),
        )
        row = cur.fetchone()

    if not row:
        return "User not found."

    if row["password_hash"] != _hash_password(old_password):
        return "Old password incorrect."

    new_hash = _hash_password(new_password)

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE users
                SET password_hash = %s
                WHERE id = %s
                  AND company_id = %s
                """,
                (new_hash, row["id"], company_id),
            )
            conn.commit()

        log_action(username_norm, "change_password", "users", ref=str(row["id"]))
        return None

    except Exception as e:
        return f"Error changing password: {e}"
