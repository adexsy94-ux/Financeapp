import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from db_config import get_db_cursor, run_migrations
from auth_module import (
    verify_user,
    create_company_and_admin,
    create_session,
    get_session_user,
    invalidate_session,
    hash_password,
)

# ============================================================
#  DB / MIGRATIONS + CORE TABLES
# ============================================================

def ensure_database_ready() -> None:
    """
    Run migrations and ensure core tables exist.
    If DB URL is missing, show a clean error and stop.
    """
    try:
        # Run schema_migrations + user_sessions migrations
        run_migrations()
    except RuntimeError:
        st.error(
            "⚠️ Database configuration error:\n\n"
            "The app could not find a valid database URL.\n\n"
            "Please set `VOUCHER_DB_URL` as an environment variable\n"
            "or configure it in Streamlit secrets "
            "(for example `VOUCHER_DB_URL` or `voucher_db_url`)."
        )
        st.stop()

    # Ensure core tables exist
    with get_db_cursor() as (conn, cur):
        # Companies
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE
            )
            """
        )

        # Users – NOTE: we don't touch existing structure if it already exists
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                company_id INTEGER REFERENCES companies(id),
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                full_name TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        # Vendors
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vendors (
                id SERIAL PRIMARY KEY,
                company_id INTEGER REFERENCES companies(id),
                name TEXT NOT NULL,
                contact_person TEXT,
                bank_name TEXT,
                bank_account TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        # Accounts
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id SERIAL PRIMARY KEY,
                company_id INTEGER REFERENCES companies(id),
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL, -- 'expense', 'payable', 'asset', etc.
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        # Vouchers
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vouchers (
                id SERIAL PRIMARY KEY,
                company_id INTEGER REFERENCES companies(id),
                created_by INTEGER REFERENCES users(id),
                voucher_number TEXT NOT NULL UNIQUE,
                vendor TEXT NOT NULL,
                requester TEXT,
                invoice TEXT,
                currency TEXT,
                gross_amount NUMERIC,
                vat_amount NUMERIC,
                wht_amount NUMERIC,
                payable_amount NUMERIC,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        # Voucher lines
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS voucher_lines (
                id SERIAL PRIMARY KEY,
                company_id INTEGER REFERENCES companies(id),
                voucher_id INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
                line_no INTEGER NOT NULL,
                description TEXT NOT NULL,
                account_code TEXT,
                amount NUMERIC NOT NULL,
                vat_percent NUMERIC,
                wht_percent NUMERIC,
                vat_amount NUMERIC,
                wht_amount NUMERIC,
                payable NUMERIC
            )
            """
        )

        # Invoices
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id SERIAL PRIMARY KEY,
                company_id INTEGER REFERENCES companies(id),
                created_by INTEGER REFERENCES users(id),
                invoice_number TEXT NOT NULL UNIQUE,
                vendor TEXT NOT NULL,
                summary TEXT,
                vatable_amount NUMERIC,
                vat_percent NUMERIC,
                vat_amount NUMERIC,
                wht_percent NUMERIC,
                wht_amount NUMERIC,
                non_vatable_amount NUMERIC,
                subtotal NUMERIC,
                total_amount NUMERIC,
                currency TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


# ============================================================
#  SESSION / AUTH HELPERS
# ============================================================

SESSION_TOKEN_KEY = "session_token"
SESSION_USER_KEY = "current_user"


def get_current_user() -> Optional[Dict[str, Any]]:
    """
    Resolve the current logged-in user via session token.
    """
    token = st.session_state.get(SESSION_TOKEN_KEY)
    if not token:
        return None

    user = get_session_user(token)
    if not user:
        # Token invalid or expired → clear
        st.session_state.pop(SESSION_TOKEN_KEY, None)
        st.session_state.pop(SESSION_USER_KEY, None)
        return None

    st.session_state[SESSION_USER_KEY] = user
    return user


def show_initial_setup_screen() -> None:
    """
    Allow creation of the first company and admin user.
    """
    st.subheader("📦 Initial Setup – Create Company & Admin")

    with st.form("initial_setup_form"):
        company_name = st.text_input("Company Name")
        company_code = st.text_input("Company Code (short code, e.g. 'ABC')")
        admin_username = st.text_input("Admin Username")
        admin_password = st.text_input("Admin Password", type="password")
        admin_password2 = st.text_input("Confirm Admin Password", type="password")

        submitted = st.form_submit_button("Create Company & Admin")

    if submitted:
        if not company_name or not company_code or not admin_username or not admin_password:
            st.error("All fields are required.")
            return
        if admin_password != admin_password2:
            st.error("Passwords do not match.")
            return

        err = create_company_and_admin(
            company_name=company_name,
            company_code=company_code,
            admin_username=admin_username,
            admin_password=admin_password,
        )
        if err:
            st.error(err)
        else:
            st.success("Company and admin user created successfully. You can now log in.")
            st.session_state["prefill_company_code"] = company_code.strip()
            st.session_state["prefill_username"] = admin_username.strip()


def show_login_screen() -> Optional[Dict[str, Any]]:
    """
    Show the login form and perform login via auth_module.verify_user.
    """
    st.subheader("🔐 Login")

    default_company_code = st.session_state.get("prefill_company_code", "")
    default_username = st.session_state.get("prefill_username", "")

    with st.form("login_form"):
        company_code = st.text_input("Company Code", value=default_company_code)
        username = st.text_input("Username", value=default_username)
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

    if not submitted:
        return None

    user, err = verify_user(
        company_code=company_code,
        username=username,
        password=password,
    )

    if err:
        st.error(err)
        return None

    token = create_session(user_id=user["id"])
    st.session_state[SESSION_TOKEN_KEY] = token
    st.session_state[SESSION_USER_KEY] = user
    st.success(f"Welcome, {user.get('full_name') or user['username']}!")

    return user


def require_login() -> Dict[str, Any]:
    """
    Ensure DB/migrations are ready and a user is logged in.
    Shows a two-tab UI (Login / Initial Setup) if not.
    """
    ensure_database_ready()

    user = get_current_user()
    if user:
        return user

    tab_login, tab_setup = st.tabs(["Login", "Initial Setup"])

    with tab_login:
        user = show_login_screen()
        if user:
            return user

    with tab_setup:
        show_initial_setup_screen()

    st.stop()


def logout() -> None:
    """
    Invalidate session and reload app.
    """
    token = st.session_state.get(SESSION_TOKEN_KEY)
    if token:
        invalidate_session(token)

    st.session_state.pop(SESSION_TOKEN_KEY, None)
    st.session_state.pop(SESSION_USER_KEY, None)
    st.experimental_rerun()


# ============================================================
#  VOUCHERS
# ============================================================

def generate_voucher_number(company_id: int) -> str:
    """
    Generate a simple voucher number: VCH-YYYY-XXXX.
    """
    today = datetime.date.today()
    year = today.year

    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT COUNT(*) FROM vouchers
            WHERE company_id = %s AND EXTRACT(YEAR FROM created_at) = %s
            """,
            (company_id, year),
        )
        count = cur.fetchone()[0] or 0

    return f"VCH-{year}-{count + 1:04d}"


def create_voucher_ui(user: Dict[str, Any]) -> None:
    st.markdown("### ✏️ Create Voucher")

    with st.form("voucher_form"):
        vendor = st.text_input("Vendor Name")
        requester = st.text_input("Requester")
        invoice = st.text_input("Invoice Number")
        currency = st.selectbox("Currency", ["₦", "$", "€", "£"], index=0)

        st.markdown("**Voucher Line** (single line for simplicity)")
        description = st.text_input("Description")
        account_code = st.text_input("Account Code")
        amount = st.number_input("Amount", min_value=0.0, format="%.2f")
        vat_percent = st.number_input("VAT %", min_value=0.0, max_value=100.0, value=7.5, format="%.2f")
        wht_percent = st.number_input("WHT %", min_value=0.0, max_value=100.0, value=0.0, format="%.2f")

        submitted = st.form_submit_button("Save Voucher")

    if not submitted:
        return

    if not vendor or amount <= 0:
        st.error("Vendor and a positive amount are required.")
        return

    vat_amount = amount * vat_percent / 100.0
    wht_amount = amount * wht_percent / 100.0
    payable = amount + vat_amount - wht_amount
    gross_amount = amount

    company_id = user.get("company_id")

    voucher_number = generate_voucher_number(company_id)

    with get_db_cursor() as (conn, cur):
        # Insert into vouchers
        cur.execute(
            """
            INSERT INTO vouchers (
                company_id, created_by, voucher_number,
                vendor, requester, invoice, currency,
                gross_amount, vat_amount, wht_amount, payable_amount, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                company_id,
                user["id"],
                voucher_number,
                vendor,
                requester,
                invoice,
                currency,
                gross_amount,
                vat_amount,
                wht_amount,
                payable,
                "draft",
            ),
        )
        voucher_id = cur.fetchone()[0]

        # Insert single line
        cur.execute(
            """
            INSERT INTO voucher_lines (
                company_id, voucher_id, line_no,
                description, account_code, amount,
                vat_percent, wht_percent, vat_amount, wht_amount, payable
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                company_id,
                voucher_id,
                1,
                description,
                account_code,
                amount,
                vat_percent,
                wht_percent,
                vat_amount,
                wht_amount,
                payable,
            ),
        )

    st.success(f"Voucher {voucher_number} created successfully.")


def list_vouchers_ui(user: Dict[str, Any]) -> None:
    st.markdown("### 📄 Recent Vouchers")

    company_id = user.get("company_id")
    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT
                voucher_number,
                vendor,
                requester,
                invoice,
                currency,
                gross_amount,
                vat_amount,
                wht_amount,
                payable_amount,
                status,
                created_at
            FROM vouchers
            WHERE company_id = %s
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    if not rows:
        st.info("No vouchers found yet.")
        return

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)


def vouchers_page(user: Dict[str, Any]) -> None:
    col1, col2 = st.columns(2)
    with col1:
        create_voucher_ui(user)
    with col2:
        list_vouchers_ui(user)


# ============================================================
#  INVOICES
# ============================================================

def generate_invoice_number(company_id: int) -> str:
    today = datetime.date.today()
    year = today.year

    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT COUNT(*) FROM invoices
            WHERE company_id = %s AND EXTRACT(YEAR FROM created_at) = %s
            """,
            (company_id, year),
        )
        count = cur.fetchone()[0] or 0

    return f"INV-{year}-{count + 1:04d}"


def create_invoice_ui(user: Dict[str, Any]) -> None:
    st.markdown("### ✏️ Create Invoice")

    with st.form("invoice_form"):
        vendor = st.text_input("Vendor Name")
        summary = st.text_input("Summary / Description")
        currency = st.selectbox("Currency", ["₦", "$", "€", "£"], index=0)

        vatable_amount = st.number_input("Vatable Amount", min_value=0.0, format="%.2f")
        vat_percent = st.number_input("VAT %", min_value=0.0, max_value=100.0, value=7.5, format="%.2f")
        wht_percent = st.number_input("WHT %", min_value=0.0, max_value=100.0, value=0.0, format="%.2f")
        non_vatable_amount = st.number_input("Non-vatable Amount", min_value=0.0, format="%.2f")

        submitted = st.form_submit_button("Save Invoice")

    if not submitted:
        return

    if not vendor or (vatable_amount + non_vatable_amount) <= 0:
        st.error("Vendor and a positive total amount are required.")
        return

    vat_amount = vatable_amount * vat_percent / 100.0
    wht_amount = vatable_amount * wht_percent / 100.0
    subtotal = vatable_amount + non_vatable_amount
    total_amount = subtotal + vat_amount - wht_amount

    company_id = user.get("company_id")
    invoice_number = generate_invoice_number(company_id)

    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            INSERT INTO invoices (
                company_id, created_by, invoice_number,
                vendor, summary,
                vatable_amount, vat_percent, vat_amount,
                wht_percent, wht_amount,
                non_vatable_amount, subtotal, total_amount,
                currency
            )
            VALUES (
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s
            )
            """,
            (
                company_id,
                user["id"],
                invoice_number,
                vendor,
                summary,
                vatable_amount,
                vat_percent,
                vat_amount,
                wht_percent,
                wht_amount,
                non_vatable_amount,
                subtotal,
                total_amount,
                currency,
            ),
        )

    st.success(f"Invoice {invoice_number} created successfully.")


def list_invoices_ui(user: Dict[str, Any]) -> None:
    st.markdown("### 📄 Recent Invoices")

    company_id = user.get("company_id")
    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT
                invoice_number,
                vendor,
                summary,
                currency,
                subtotal,
                vat_amount,
                wht_amount,
                total_amount,
                created_at
            FROM invoices
            WHERE company_id = %s
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    if not rows:
        st.info("No invoices found yet.")
        return

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)


def invoices_page(user: Dict[str, Any]) -> None:
    col1, col2 = st.columns(2)
    with col1:
        create_invoice_ui(user)
    with col2:
        list_invoices_ui(user)


# ============================================================
#  CRM: VENDORS & ACCOUNTS
# ============================================================

def vendors_tab(user: Dict[str, Any]) -> None:
    st.markdown("### 🧾 Vendors")

    company_id = user.get("company_id")

    with st.form("vendor_form"):
        name = st.text_input("Vendor Name")
        contact_person = st.text_input("Contact Person")
        bank_name = st.text_input("Bank Name")
        bank_account = st.text_input("Bank Account")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save Vendor")

    if submitted:
        if not name:
            st.error("Vendor name is required.")
        else:
            with get_db_cursor() as (conn, cur):
                cur.execute(
                    """
                    INSERT INTO vendors (
                        company_id, name, contact_person,
                        bank_name, bank_account, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (company_id, name, contact_person, bank_name, bank_account, notes),
                )
            st.success("Vendor saved.")

    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT
                name,
                contact_person,
                bank_name,
                bank_account,
                notes,
                created_at
            FROM vendors
            WHERE company_id = %s
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No vendors yet.")


def accounts_tab(user: Dict[str, Any]) -> None:
    st.markdown("### 📚 Accounts")

    company_id = user.get("company_id")

    with st.form("account_form"):
        code = st.text_input("Account Code")
        name = st.text_input("Account Name")
        acc_type = st.selectbox("Type", ["expense", "payable", "asset", "revenue"], index=0)
        submitted = st.form_submit_button("Save Account")

    if submitted:
        if not code or not name:
            st.error("Account code and name are required.")
        else:
            with get_db_cursor() as (conn, cur):
                cur.execute(
                    """
                    INSERT INTO accounts (
                        company_id, code, name, type
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (company_id, code, name, acc_type),
                )
            st.success("Account saved.")

    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT
                code,
                name,
                type,
                created_at
            FROM accounts
            WHERE company_id = %s
            ORDER BY code
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No accounts yet.")


def crm_page(user: Dict[str, Any]) -> None:
    tab_vendors, tab_accounts = st.tabs(["Vendors", "Accounts"])

    with tab_vendors:
        vendors_tab(user)

    with tab_accounts:
        accounts_tab(user)


# ============================================================
#  REPORTS
# ============================================================

def reports_page(user: Dict[str, Any]) -> None:
    st.markdown("### 📊 Simple Reports")

    company_id = user.get("company_id")

    with get_db_cursor() as (conn, cur):
        # Voucher totals
        cur.execute(
            """
            SELECT
                COALESCE(SUM(gross_amount), 0) AS gross_total,
                COALESCE(SUM(vat_amount), 0) AS vat_total,
                COALESCE(SUM(wht_amount), 0) AS wht_total,
                COALESCE(SUM(payable_amount), 0) AS payable_total
            FROM vouchers
            WHERE company_id = %s
            """,
            (company_id,),
        )
        v_row = cur.fetchone()

        # Invoice totals
        cur.execute(
            """
            SELECT
                COALESCE(SUM(subtotal), 0) AS subtotal_total,
                COALESCE(SUM(vat_amount), 0) AS vat_total,
                COALESCE(SUM(wht_amount), 0) AS wht_total,
                COALESCE(SUM(total_amount), 0) AS total_total
            FROM invoices
            WHERE company_id = %s
            """,
            (company_id,),
        )
        i_row = cur.fetchone()

    st.write("#### Voucher Summary")
    st.write(
        {
            "Gross": float(v_row["gross_total"]),
            "VAT": float(v_row["vat_total"]),
            "WHT": float(v_row["wht_total"]),
            "Payable": float(v_row["payable_total"]),
        }
    )

    st.write("#### Invoice Summary")
    st.write(
        {
            "Subtotal": float(i_row["subtotal_total"]),
            "VAT": float(i_row["vat_total"]),
            "WHT": float(i_row["wht_total"]),
            "Total": float(i_row["total_total"]),
        }
    )


# ============================================================
#  USER MANAGEMENT (ADMIN ONLY)
# ============================================================

def user_management_page(current_user: Dict[str, Any]) -> None:
    if not current_user.get("is_admin"):
        st.warning("User Management is only available for admins.")
        return

    st.markdown("### 👥 User Management")

    company_id = current_user.get("company_id")

    st.write("#### Create User")
    with st.form("create_user_form"):
        username = st.text_input("Username")
        full_name = st.text_input("Full Name")
        password = st.text_input("Password", type="password")
        role = st.selectbox("Role", ["user", "admin"], index=0)
        is_active = st.checkbox("Active", value=True)
        submitted = st.form_submit_button("Create User")

    if submitted:
        if not username or not password:
            st.error("Username and password are required.")
        else:
            pw_hash = hash_password(password)
            is_admin = role == "admin"

            with get_db_cursor() as (conn, cur):
                cur.execute(
                    """
                    INSERT INTO users (
                        company_id, username, password_hash,
                        full_name, role, is_admin, is_active
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        company_id,
                        username.strip().lower(),
                        pw_hash,
                        full_name,
                        role,
                        is_admin,
                        is_active,
                    ),
                )
            st.success("User created.")

    st.write("#### Existing Users")
    with get_db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT
                id,
                username,
                full_name,
                role,
                is_admin,
                is_active,
                created_at
            FROM users
            WHERE company_id = %s
            ORDER BY username
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No users found for this company.")


# ============================================================
#  DB BROWSER (ADMIN ONLY)
# ============================================================

def db_browser_page(current_user: Dict[str, Any]) -> None:
    if not current_user.get("is_admin"):
        st.warning("DB Browser is only available for admins.")
        return

    st.markdown("### 🛠️ DB Browser (Admin Only)")
    default_query = "SELECT * FROM vouchers ORDER BY id DESC LIMIT 50;"

    query = st.text_area("SQL Query", value=default_query, height=200)
    if st.button("Run Query"):
        with get_db_cursor() as (conn, cur):
            try:
                cur.execute(query)
                if cur.description:
                    rows = cur.fetchall()
                    df = pd.DataFrame(rows)
                    st.dataframe(df, use_container_width=True)
                else:
                    st.success("Query executed successfully (no result set).")
            except Exception as ex:
                st.error(f"SQL error: {ex}")


# ============================================================
#  MAIN
# ============================================================

def main() -> None:
    st.set_page_config(
        page_title="VoucherPro Finance App",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    user = require_login()

    # Sidebar info
    with st.sidebar:
        st.markdown(f"**Logged in as:** {user.get('full_name') or user['username']}")
        st.markdown(f"**Role:** {user.get('role', 'user')}")
        if st.button("🚪 Log out"):
            logout()

    tabs = st.tabs(
        [
            "Vouchers",
            "Invoices",
            "CRM",
            "Reports",
            "User Management",
            "DB Browser",
        ]
    )

    with tabs[0]:
        vouchers_page(user)

    with tabs[1]:
        invoices_page(user)

    with tabs[2]:
        crm_page(user)

    with tabs[3]:
        reports_page(user)

    with tabs[4]:
        user_management_page(user)

    with tabs[5]:
        db_browser_page(user)


if __name__ == "__main__":
    main()
