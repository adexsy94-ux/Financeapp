# app_main.py – Finance app main entrypoint

import streamlit as st
import pandas as pd

# --- Safety alias: allow old code to call st.markmarkdown without crashing ---
if not hasattr(st, "markmarkdown"):
    st.markmarkdown = st.markdown

from db_config import init_schema, connect
from auth_module import (
    init_auth,
    require_login,
    require_admin,
    require_permission,
    current_user,
    list_users,
    create_user_for_company,
)
from crm_gateway import (
    list_staff,
    upsert_staff,
    list_vendors,
    upsert_vendor,
    list_accounts,
    upsert_account,
    get_vendor_name_list,
    get_requester_options,
    get_payable_account_options,
    get_expense_asset_account_options,
)
from vouchers_module import (
    list_vouchers,
    create_voucher,
    change_voucher_status,
    delete_voucher,
)
from invoices_module import (
    list_invoices,
    create_invoice,
    update_invoice,
    delete_invoice,
)
from pdf_utils import build_voucher_pdf_bytes

# ... rest of your app_main.py (app_vouchers, app_invoices, app_crm, main(), etc.)


# -------------------
# Helpers
# -------------------

def logout_button():
    if st.sidebar.button("Logout"):
        st.session_state["user"] = None
        st.experimental_rerun()


# -------------------
# Vouchers
# -------------------

def app_vouchers():
    require_permission("can_create_voucher")
    user = current_user()
    username = user["username"]
    company_id = user["company_id"]

    # CRM-driven dropdown options
    vendor_options = get_vendor_name_list(company_id)
    requester_options = get_requester_options(company_id)
    account_options = get_expense_asset_account_options(company_id)

    st.subheader("Create Voucher")

    vendor = st.selectbox("Vendor (from CRM)", vendor_options)
    requester = st.selectbox("Requester (Staff in CRM)", requester_options)

    # -----------------------------
    # Link voucher to invoice
    # -----------------------------
    all_invoices = list_invoices(company_id=company_id)
    invoice_numbers_for_vendor = [
        row["invoice_number"]
        for row in all_invoices
        if row.get("vendor") == vendor
    ]
    invoice_choices = ["(None)"] + invoice_numbers_for_vendor
    invoice_choice = st.selectbox(
        "Invoice / Reference (all invoices for selected vendor)",
        invoice_choices,
    )
    invoice_ref = "" if invoice_choice == "(None)" else invoice_choice

    # Find the selected invoice row (if any)
    selected_invoice = None
    for inv in all_invoices:
        if inv.get("invoice_number") == invoice_choice:
            selected_invoice = inv
            break

    # Determine invoice currency if invoice is selected
    invoice_currency = None
    if selected_invoice is not None:
        invoice_currency = (selected_invoice.get("currency") or "NGN").upper()

    # Balances we’ll use for validation
    actual_balance = None
    vat_balance = None
    wht_balance = None

    # ---- Invoice allocation summary (amounts, paid, balances) ----
    if selected_invoice is not None:
        inv_vatable = float(selected_invoice.get("vatable_amount") or 0.0)
        inv_non_vatable = float(selected_invoice.get("non_vatable_amount") or 0.0)
        inv_vat_total = float(selected_invoice.get("vat_amount") or 0.0)
        inv_wht_total = float(selected_invoice.get("wht_amount") or 0.0)

        # Base (invoice) amount = vatable + non-vatable
        actual_total = inv_vatable + inv_non_vatable

        amount_paid = 0.0
        vat_paid = 0.0
        wht_paid = 0.0

        # Sum voucher_lines for vouchers referencing this invoice (same company + currency, non-rejected)
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            COALESCE(SUM(vl.amount), 0)     AS amount_paid,
                            COALESCE(SUM(vl.vat_value), 0)  AS vat_paid,
                            COALESCE(SUM(vl.wht_value), 0)  AS wht_paid
                        FROM voucher_lines vl
                        JOIN vouchers v
                          ON v.id = vl.voucher_id
                        WHERE v.company_id = %s
                          AND v.invoice_ref = %s
                          AND v.currency = %s
                          AND (v.status IS NULL OR v.status <> 'rejected')
                        """,
                        (
                            company_id,
                            invoice_choice,
                            invoice_currency or "NGN",
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        amount_paid = float(row[0] or 0.0)
                        vat_paid = float(row[1] or 0.0)
                        wht_paid = float(row[2] or 0.0)
        except Exception as e:
            st.warning(f"Could not compute invoice allocation summary: {e}")

        # Balances
        actual_balance = actual_total - amount_paid
        vat_balance = inv_vat_total - vat_paid
        wht_balance = inv_wht_total - wht_paid

        cur_code = invoice_currency or "NGN"

        st.markdown("### Invoice Allocation Summary")

        c1, c2, c3 = st.columns(3)

        # -------- Base amount block --------
        with c1:
            st.markdown("**Base Amount (Vatable + Non-vatable)**")
            st.write(f"Invoice Amount: **{actual_total:,.2f} {cur_code}**")
            st.markdown(
                "Amount Paid via Vouchers: "
                f"<span style='color: green; font-weight:bold;'>{amount_paid:,.2f} {cur_code}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "Balance to Pay: "
                f"<span style='color: red; font-weight:bold;'>{actual_balance:,.2f} {cur_code}</span>",
                unsafe_allow_html=True,
            )

        # -------- VAT block --------
        with c2:
            st.markdown("**VAT Allocation**")
            st.write(f"Invoice VAT: **{inv_vat_total:,.2f} {cur_code}**")
            st.markdown(
                "VAT Paid via Vouchers: "
                f"<span style='color: green; font-weight:bold;'>{vat_paid:,.2f} {cur_code}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "VAT Balance: "
                f"<span style='color: red; font-weight:bold;'>{vat_balance:,.2f} {cur_code}</span>",
                unsafe_allow_html=True,
            )

        # -------- WHT block --------
       
    # -----------------------------
    # Voucher currency and file upload
    # -----------------------------
    currencies = ["NGN", "USD", "GBP", "EUR"]
    default_currency = invoice_currency or "NGN"
    try:
        default_index = currencies.index(default_currency)
    except ValueError:
        default_index = 0

    currency = st.selectbox(
        "Currency",
        currencies,
        index=default_index,
        help="Auto-fills from the selected invoice if available, but you can override.",
    )

    uploaded = st.file_uploader(
        "Attach supporting document (optional)", type=["pdf", "jpg", "png"]
    )
    file_name = None
    file_bytes = None
    if uploaded is not None:
        file_name = uploaded.name
        file_bytes = uploaded.read()

    st.markdown("**Voucher Lines**")
    lines = []
    num_lines = st.number_input(
        "Number of lines", min_value=1, max_value=20, value=1, step=1
    )
    for i in range(int(num_lines)):
        st.markdown(f"**Line {i+1}**")
        col1, col2, col3, col4, col5 = st.columns([3, 1, 2, 1, 1])
        with col1:
            desc = st.text_input("Description", key=f"line_desc_{i}")
        with col2:
            amt = st.number_input(
                "Amount", key=f"line_amt_{i}", min_value=0.0, step=0.01
            )
        with col3:
            acct = st.selectbox(
                "Expense / Asset Account (Chart of Accounts)",
                account_options,
                key=f"line_acct_{i}",
            )
        with col4:
            vat = st.number_input(
                "VAT %", key=f"line_vat_{i}", min_value=0.0, step=0.5
            )
        with col5:
            wht = st.number_input(
                "WHT %", key=f"line_wht_{i}", min_value=0.0, step=0.5
            )

        lines.append(
            {
                "description": desc,
                "amount": float(amt or 0.0),
                "account_code": acct,
                "vat_percent": float(vat or 0.0),
                "wht_percent": float(wht or 0.0),
            }
        )

    # ---- Pre-calculate totals on the current voucher lines ----
    total_line_amount = sum((l.get("amount") or 0.0) for l in lines)
    total_line_vat = sum(
        round((l.get("amount") or 0.0) * (l.get("vat_percent") or 0.0) / 100.0, 2)
        for l in lines
    )
    total_line_wht = sum(
        round((l.get("amount") or 0.0) * (l.get("wht_percent") or 0.0) / 100.0, 2)
        for l in lines
    )

    st.markdown("### Voucher Line Totals (for this document)")
    st.write(f"Total Base Amount: **{total_line_amount:,.2f} {currency}**")
    st.write(f"Total VAT Amount (@line rates): **{total_line_vat:,.2f} {currency}**")
    st.write(f"Total WHT Amount (@line rates): **{total_line_wht:,.2f} {currency}**")

    # ---- Validation against invoice balances ----
    validation_errors = []
    if selected_invoice is not None:
        cur_code = invoice_currency or "NGN"

        # Base amount (Amount column) vs Base Balance
        if actual_balance is not None and total_line_amount > actual_balance + 0.0001:
            validation_errors.append(
                f"Total Amount in voucher lines ({total_line_amount:,.2f} {cur_code}) "
                f"is greater than the Base Balance to Pay ({actual_balance:,.2f} {cur_code})."
            )

        # VAT vs VAT Balance
        if vat_balance is not None and total_line_vat > vat_balance + 0.0001:
            validation_errors.append(
                f"Total VAT amount in voucher lines ({total_line_vat:,.2f} {cur_code}) "
                f"is greater than the VAT Balance ({vat_balance:,.2f} {cur_code})."
            )

        # WHT vs WHT Balance
        if wht_balance is not None and total_line_wht > wht_balance + 0.0001:
            validation_errors.append(
                f"Total WHT amount in voucher lines ({total_line_wht:,.2f} {cur_code}) "
                f"is greater than the WHT Balance ({wht_balance:,.2f} {cur_code})."
            )

    if validation_errors:
        for msg in validation_errors:
            st.error(msg)

    # ---- Save button (only actually saves if validation passes) ----
    if st.button("Save Voucher"):
        if validation_errors:
            st.error(
                "Voucher not saved because one or more line totals are higher than the "
                "remaining invoice balances shown above. Please adjust the Amount, VAT, "
                "or WHT so they are within the balances."
            )
        else:
            err = create_voucher(
                company_id=company_id,
                username=username,
                vendor=vendor,
                requester=requester,
                invoice_ref=invoice_ref,
                currency=currency,
                lines=lines,
                file_name=file_name,
                file_bytes=file_bytes,
            )
            if err:
                st.error(err)
            else:
                st.success("Voucher created successfully.")
                st.experimental_rerun()

    st.markdown("---")
    st.subheader("Recent Vouchers")

    vdf = pd.DataFrame(list_vouchers(company_id=company_id))
    if not vdf.empty:
        display_cols = [c for c in vdf.columns if c not in ("file_data",)]
        st.dataframe(vdf[display_cols])

        st.markdown("**Update Voucher Status**")

        col1, col2, col3 = st.columns(3)
        with col1:
            selected_id = st.number_input(
                "Voucher ID",
                min_value=0,
                step=1,
                value=0,
                help="Enter the voucher ID you want to act on.",
            )
        with col2:
            action = st.selectbox(
                "Action",
                ["--", "Submit for approval", "Mark as draft", "Approve", "Reject"],
            )
        with col3:
            st.write(" ")

        if st.button("Apply Action on Voucher"):
            if selected_id <= 0:
                st.error("Please enter a valid voucher ID.")
            elif action == "--":
                st.error("Please select an action.")
            else:
                new_status = None

                if action == "Submit for approval":
                    new_status = "submitted"
                elif action == "Mark as draft":
                    new_status = "draft"
                elif action == "Approve":
                    require_permission("can_approve_voucher")
                    new_status = "approved"
                elif action == "Reject":
                    require_permission("can_approve_voucher")
                    new_status = "rejected"

                if new_status is None:
                    st.error("Unknown action.")
                else:
                    err = change_voucher_status(
                        company_id=company_id,
                        voucher_id=int(selected_id),
                        new_status=new_status,
                        actor_username=username,
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success(
                            f"Voucher {selected_id} updated to status '{new_status}'."
                        )
                        st.experimental_rerun()

        st.markdown("**Export to PDF**")
        pdf_id = st.number_input(
            "Voucher ID to export",
            min_value=0,
            step=1,
            value=0,
            key="pdf_voucher_id",
        )
        if pdf_id > 0 and st.button("Download Voucher PDF"):
            try:
                pdf_bytes = build_voucher_pdf_bytes(company_id=company_id, voucher_id=int(pdf_id))
            except Exception as e:
                st.error(f"Error generating PDF: {e}")
            else:
                st.download_button(
                    label="Download PDF",
                    data=pdf_bytes,
                    file_name=f"voucher_{pdf_id}.pdf",
                    mime="application/pdf",
                )
    else:
        st.info("No vouchers yet.")


# -------------------
# Invoices
# -------------------

def app_invoices():
    require_permission("can_create_voucher")  # or dedicated invoice permission
    user = current_user()
    username = user["username"]
    company_id = user["company_id"]

    vendor_options = get_vendor_name_list(company_id)
    payable_options = get_payable_account_options(company_id)
    expense_asset_options = get_expense_asset_account_options(company_id)

    st.subheader("Create Invoice")

    st.info("Invoice number will be auto-generated using date and time when you save.")

    vendor = st.selectbox("Vendor", vendor_options)
    invoice_date = st.date_input("Invoice Date")
    due_date = st.date_input("Due Date")
    vendor_invoice_number = st.text_input("Vendor Invoice Number")
    summary = st.text_area("Summary")

    vatable_amount = st.number_input("Vatable Amount", min_value=0.0, step=0.01)
    vat_rate = st.number_input("VAT Rate (%)", min_value=0.0, step=0.5)
    wht_rate = st.number_input("WHT Rate (%)", min_value=0.0, step=0.5)
    non_vatable_amount = st.number_input(
        "Non-vatable Amount", min_value=0.0, step=0.01
    )

    terms = st.text_area("Terms")
    currency = st.selectbox("Currency", ["NGN", "USD", "GBP", "EUR"], index=0)

    payable_account = st.selectbox(
        "Payable Account (Chart of Accounts)", payable_options
    )
    expense_asset_account = st.selectbox(
        "Expense / Asset Account (Chart of Accounts)",
        expense_asset_options,
    )

    uploaded = st.file_uploader(
        "Attach invoice document (optional)",
        type=["pdf", "jpg", "png"],
        key="inv_file",
    )
    file_name = None
    file_bytes = None
    if uploaded is not None:
        file_name = uploaded.name
        file_bytes = uploaded.read()

    if st.button("Save Invoice"):
        err = None
        try:
            err = create_invoice(
                company_id=company_id,
                username=username,
                vendor=vendor,
                invoice_date=invoice_date,
                due_date=due_date,
                vendor_invoice_number=vendor_invoice_number,
                summary=summary,
                vatable_amount=vatable_amount,
                vat_rate=vat_rate,
                wht_rate=wht_rate,
                non_vatable_amount=non_vatable_amount,
                terms=terms,
                payable_account=payable_account,
                expense_asset_account=expense_asset_account,
                currency=currency,
                file_name=file_name,
                file_data=file_bytes,
            )
        except Exception as ex:
            err = str(ex)

        if err:
            st.error(err)
        else:
            st.success("Invoice created successfully.")
            st.experimental_rerun()

    st.markdown("---")
    st.subheader("Recent Invoices")
    idf = pd.DataFrame(list_invoices(company_id=company_id))
    if not idf.empty:
        if "total_amount" in idf.columns:
            idf["total_amount_fmt"] = idf["total_amount"].apply(
                lambda v: f"{v:,.2f}" if v is not None else ""
            )
        st.dataframe(idf)
    else:
        st.info("No invoices yet.")


# -------------------
# CRM
# -------------------

def app_crm():
    require_permission("can_create_voucher")
    user = current_user()
    username = user["username"]
    company_id = user["company_id"]

    # ---- Staff ----
    st.subheader("Staff")

    with st.form("staff_form"):
        col1, col2 = st.columns(2)
        with col1:
            first_name = st.text_input("First Name")
            last_name = st.text_input("Last Name")
            email = st.text_input("Email")
            phone = st.text_input("Phone")
        with col2:
            status = st.selectbox("Status", ["Active", "Inactive"], index=0)
            position = st.text_input("Position / Role")

        submitted_staff = st.form_submit_button("Save Staff")
        if submitted_staff:
            err = upsert_staff(
                company_id=company_id,
                staff_id=None,
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                status=status,
                position=position,
            )
            if err:
                st.error(err)
            else:
                st.success("Staff saved.")

    staff_rows = list_staff(company_id=company_id)
    if staff_rows:
        st.dataframe(pd.DataFrame(staff_rows))
    else:
        st.info("No staff yet.")

    st.markdown("---")
    # ---- Vendors ----
    st.subheader("Vendors")

    with st.form("vendor_form"):
        name = st.text_input("Vendor Name")
        contact = st.text_input("Contact Person")
        bank_name = st.text_input("Bank Name")
        bank_account = st.text_input("Bank Account")
        notes = st.text_area("Notes")
        submitted_vendor = st.form_submit_button("Save Vendor")
        if submitted_vendor:
            if not name:
                st.error("Vendor name is required.")
            else:
                err = upsert_vendor(
                    company_id=company_id,
                    name=name,
                    contact_person=contact,
                    bank_name=bank_name,
                    bank_account=bank_account,
                    notes=notes,
                    username=username,
                )
                if err:
                    st.error(err)
                else:
                    st.success("Vendor saved.")

    vdf = pd.DataFrame(list_vendors(company_id=company_id))
    if not vdf.empty:
        st.dataframe(vdf)
    else:
        st.info("No vendors yet.")

    st.markdown("---")
    # ---- Chart of Accounts ----
    st.subheader("Chart of Accounts")

    with st.form("account_form"):
        code = st.text_input("Account Code")
        name = st.text_input("Account Name")
        acc_type = st.selectbox(
            "Account Type",
            ["asset", "liability", "equity", "income", "expense"],
            index=0,
        )
        submitted_account = st.form_submit_button("Save Account")
        if submitted_account:
            if not code or not name:
                st.error("Code and name are required.")
            else:
                err = upsert_account(
                    company_id=company_id,
                    code=code,
                    name=name,
                    account_type=acc_type,
                    username=username,
                )
                if err:
                    st.error(err)
                else:
                    st.success("Account saved.")

    all_accounts = list_accounts(company_id=company_id)
    if all_accounts:
        adf = pd.DataFrame(all_accounts)
        st.dataframe(adf)
    else:
        st.info("No accounts yet.")


# -------------------
# User Management
# -------------------

def app_user_management():
    require_permission("can_manage_users")
    admin = current_user()
    admin_name = admin["username"]
    company_id = admin["company_id"]

    st.subheader(
        f"User Management – {admin.get('company_name','')} ({admin.get('company_code','')})"
    )

    st.markdown("### Create New User")

    with st.form("create_user_form"):
        new_username = st.text_input("Username")
        pw1 = st.text_input("Password", type="password")
        pw2 = st.text_input("Confirm Password", type="password")
        new_role = st.selectbox("Role", ["user", "admin"], index=0)
        can_create_voucher = st.checkbox("Can create vouchers", value=True)
        can_approve_voucher = st.checkbox("Can approve vouchers", value=False)
        can_manage_users = st.checkbox("Can manage users", value=False)
        submitted = st.form_submit_button("Create User")

    if submitted:
        if not new_username or not pw1 or not pw2:
            st.error("Username and both password fields are required.")
        elif pw1 != pw2:
            st.error("Passwords do not match.")
        else:
            err = create_user_for_company(
                company_id=company_id,
                username=new_username,
                password=pw1,
                role=new_role,
                can_create_voucher=can_create_voucher,
                can_approve_voucher=can_approve_voucher,
                can_manage_users=can_manage_users,
                actor_username=admin_name,
            )
            if err:
                st.error(err)
            else:
                st.success(f"User '{new_username}' created.")

    st.markdown("---")
    st.markdown("### Existing Users")

    users = list_users(company_id=company_id)
    if not users:
        st.info("No users found.")
        return

    for u in users:
        st.markdown(f"**{u['username']}** – {u.get('role','')}")
        col1, col2, col3 = st.columns(3)
        with col1:
            c_create = st.checkbox(
                "Can create vouchers",
                value=bool(u.get("can_create_voucher")),
                key=f"edit_create_{u['id']}",
            )
        with col2:
            c_approve = st.checkbox(
                "Can approve vouchers",
                value=bool(u.get("can_approve_voucher")),
                key=f"edit_approve_{u['id']}",
            )
        with col3:
            c_manage = st.checkbox(
                "Can manage users",
                value=bool(u.get("can_manage_users")),
                key=f"edit_manage_{u['id']}",
            )
        # NOTE: updating existing permissions would need an update_user_permissions()
        # which is not wired here to keep this example simple.


# -------------------
# Main
# -------------------

def main():
    st.set_page_config(page_title="VoucherPro – Multi-Company", layout="wide")

    if "user" not in st.session_state:
        st.session_state["user"] = None

    init_schema()
    init_auth()

    require_login()
    user = current_user()

    st.sidebar.markdown(
        f"**User:** {user['username']}  "
        f"<br/>**Company:** {user.get('company_name','')} ({user.get('company_code','')})",
        unsafe_allow_html=True,
    )
    logout_button()

    menu = ["Vouchers", "Invoices", "CRM", "User Management"]
    choice = st.sidebar.radio("Go to", menu)

    if choice == "Vouchers":
        app_vouchers()
    elif choice == "Invoices":
        app_invoices()
    elif choice == "CRM":
        app_crm()
    elif choice == "User Management":
        app_user_management()


if __name__ == "__main__":
    main()
