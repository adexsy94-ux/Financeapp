# auth_module.py
# Authentication + role-based access helpers + user management

import hashlib
from contextlib import closing
from typing import Optional, Dict, List

import streamlit as st

from db_config import connect, AUTH_TABLE_SQL, log_action


# ------------------------
# Password hashing
# ------------------------

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# ------------------------
# User table
# ------------------------

def init_auth():
    """
    Ensure the users table exists (duplicated from db_config for safety).
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(AUTH_TABLE_SQL)
            conn.commit()
    except Exception:
        pass


def create_user(username: str, password: str, is_admin: bool = False) -> Optional[str]:
    """
    Create a new user.
    Return error message or None on success.
    """
    username_norm = (username or "").strip().lower()
    if not username_norm or not password:
        return "Username and password are required."

    pw_hash = _hash_password(password)

    role = "admin" if is_admin else "user"
    can_create_voucher = True
    can_approve_voucher = bool(is_admin)
    can_manage_users = bool(is_admin)

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO users (username, password_hash, role,
                                   can_create_voucher, can_approve_voucher, can_manage_users)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    username_norm,
                    pw_hash,
                    role,
                    can_create_voucher,
                    can_approve_voucher,
                    can_manage_users,
                ),
            )
            conn.commit()
        log_action(username_norm, "create_user", "users", ref=username_norm)
        return None
    except Exception as e:
        return f"Error creating user: {e}"


def verify_user(username: str, password: str) -> bool:
    username_norm = (username or "").strip().lower()
    if not username_norm or not password:
        return False

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                "SELECT password_hash FROM users WHERE username = %s",
                (username_norm,),
            )
            row = cur.fetchone()
    except Exception:
        return False

    if not row:
        return False

    stored_hash = row["password_hash"]
    return stored_hash == _hash_password(password)


def get_user_record(username: str) -> Optional[Dict]:
    username_norm = (username or "").strip().lower()
    if not username_norm:
        return None

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                SELECT id, username, role,
                       can_create_voucher,
                       can_approve_voucher,
                       can_manage_users
                FROM users
                WHERE username = %s
                """,
                (username_norm,),
            )
            row = cur.fetchone()
    except Exception:
        return None

    if not row:
        return None

    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "can_create_voucher": bool(row["can_create_voucher"]),
        "can_approve_voucher": bool(row["can_approve_voucher"]),
        "can_manage_users": bool(row["can_manage_users"]),
    }


def list_users() -> List[Dict]:
    """
    Return all users for admin user-management.
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
            ORDER BY username
            """
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def update_user_permissions(
    actor_username: str,
    user_id: int,
    role: str,
    can_create_voucher: bool,
    can_approve_voucher: bool,
    can_manage_users: bool,
) -> Optional[str]:
    """
    Update role and permission flags for a user.
    """
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
                """,
                (
                    role,
                    bool(can_create_voucher),
                    bool(can_approve_voucher),
                    bool(can_manage_users),
                    user_id,
                ),
            )
            conn.commit()
        log_action(
            actor_username,
            "update_user_permissions",
            "users",
            ref=str(user_id),
            details=f"role={role}, create={can_create_voucher}, approve={can_approve_voucher}, manage_users={can_manage_users}",
        )
        return None
    except Exception as e:
        return f"Error updating permissions: {e}"


def change_password(username: str, old_password: str, new_password: str) -> Optional[str]:
    """
    Change the password for a user if the old password is correct.
    Returns error message or None on success.
    """
    username_norm = (username or "").strip().lower()
    if not username_norm or not old_password or not new_password:
        return "All fields are required."

    # Verify old password
    if not verify_user(username_norm, old_password):
        return "Old password is incorrect."

    new_hash = _hash_password(new_password)

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE username = %s",
                (new_hash, username_norm),
            )
            conn.commit()
        log_action(username_norm, "change_password", "users", ref=username_norm)
        return None
    except Exception as e:
        return f"Error changing password: {e}"


# ------------------------
# Session helpers
# ------------------------

def current_user() -> Optional[Dict]:
    return st.session_state.get("user")


def require_login():
    """
    If not logged in, render login form and stop.
    """
    if current_user() is not None:
        return

    st.markdown("<h2>Voucher & CRM Login</h2>", unsafe_allow_html=True)
    tab_login, tab_signup = st.tabs(["Login", "Sign up"])

    with tab_login:
        lu = st.text_input("Username", key="login_username")
        lp = st.text_input("Password", type="password", key="login_password")
        if st.button("Login"):
            if verify_user(lu, lp):
                user_obj = get_user_record(lu)
                if not user_obj:
                    st.error("Login failed: user record not found.")
                else:
                    st.session_state["user"] = user_obj
                    st.success("Login successful.")
                    st.experimental_rerun()
            else:
                st.error("Invalid username or password.")

    with tab_signup:
        st.info("Only admin can create users. Please contact your administrator.")

    st.stop()


def require_admin():
    user = current_user()
    if not user:
        require_login()
        return
    if user["role"] != "admin":
        st.error("You do not have permission to view this page.")
        st.stop()


def require_permission(flag: str):
    """
    Example:
      require_permission("can_create_voucher")
      require_permission("can_approve_voucher")
      require_permission("can_manage_users")
    """
    user = current_user()
    if not user:
        require_login()
        return
    if not user.get(flag, False):
        st.error("You do not have permission to perform this action.")
        st.stop()
