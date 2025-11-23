# app_main.py
# Main Streamlit app wiring all modules together, with multi-tenant support

import streamlit as st
import pandas as pd
import psycopg2

from db_config import init_schema, connect
from auth_module import (
    init_auth,
    require_login,
    require_admin,
    require_permission,
    current_user,
    list_users,
    update_user_permissions,
    create_user_for_company,
)
from crm_gateway import (
    list_vendors,
    upsert_vendor,
    delete_vendor,
    list_accounts,
    upsert_account,
    delete_account,
    list_staff,
    upsert_staff,
    delete_staff,
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
    get_invoice_allocations,
    list_voucher_lines_for_invoice,
    save_voucher_attachment,
    get_voucher_attachments,
)
from invoices_module import (
    list_invoices,
    create_invoice,
    update_invoice,
    delete_invoice,
)
from pdf_utils import build_voucher_pdf_bytes


# ----------
# Helpers
# ----------

def get_connection():
    """
    Returns a psycopg2 connection using the shared connect() helper.
    All app code should call this instead of opening connections directly.
    """
    return connect()


def logout_button():
    if st.sidebar.button("Logout"):
        st.session_state["authenticated"] = False
        st.session_state["user"] = None
        st.experimental_rerun()


# ----------
# CRM (Vendors, Accounts, Staff)
# ----------

def app_crm():
    require_permission("can_manage_crm")
    user = current_user()
    company_id = user["company_id"]

    st.title("CRM & Master Data")

    tabs = st.tabs(["Vendors", "Chart of Accounts", "Staff"])

    # Vendors tab
    with tabs[0]:
        st.subheader("Vendors")
        vendors = list_vendors(company_id=company_id)
        df_v = pd.DataFrame(vendors)
        if not df_v.empty:
            st.dataframe(df_v)
        else:
            st.info("No vendors found for your company yet.")

        st.markdown("### Add / Update Vendor")
        with st.form("vendor_form"):
            vendor_id = st.text_input("Vendor ID (leave blank to create new)")
            name = st.text_input("Vendor Name")
            email = st.text_input("Email")
            phone = st.text_input("Phone")
            address = st.text_area("Address")
            submit_v = st.form_submit_button("Save Vendor")

        if submit_v:
            if not name:
                st.error("Vendor name is required.")
            else:
                upsert_vendor(
                    company_id=company_id,
                    vendor_id=vendor_id.strip() or None,
                    name=name.strip(),
                    email=email.strip(),
                    phone=phone.strip(),
                    address=address.strip(),
                )
                st.success("Vendor saved successfully.")
                st.experimental_rerun()

        st.markdown("### Delete Vendor")
        if not df_v.empty:
            vendor_to_delete = st.selectbox(
                "Select Vendor to delete",
                df_v["id"].astype(str) + " - " + df_v["name"],
            )
            if st.button("Delete Vendor"):
                vid = vendor_to_delete.split(" - ")[0]
                delete_vendor(company_id, vid)
                st.success("Vendor deleted.")
                st.experimental_rerun()
        else:
            st.info("No vendors available to delete.")

    # Chart of Accounts tab
    with tabs[1]:
        st.subheader("Chart of Accounts")
        accounts = list_accounts(company_id=company_id)
        df_a = pd.DataFrame(accounts)
        if not df_a.empty:
            st.dataframe(df_a)
        else:
            st.info("No accounts defined yet.")

        st.markdown("### Add / Update Account")
        with st.form("account_form"):
            account_id = st.text_input("Account ID (leave blank to create new)")
            code = st.text_input("Account Code")
            name = st.text_input("Account Name")
            account_type = st.selectbox(
                "Account Type",
                ["Asset", "Liability", "Equity", "Income", "Expense"],
            )
            is_payable = st.checkbox("Is Payable Account?")
            is_expense_asset = st.checkbox("Is Expense/Asset Account?")
            submit_a = st.form_submit_button("Save Account")

        if submit_a:
            if not code or not name:
                st.error("Account code and name are required.")
            else:
                upsert_account(
                    company_id=company_id,
                    account_id=account_id.strip() or None,
                    code=code.strip(),
                    name=name.strip(),
                    account_type=account_type,
                    is_payable=is_payable,
                    is_expense_asset=is_expense_asset,
                )
                st.success("Account saved successfully.")
                st.experimental_rerun()

        st.markdown("### Delete Account")
        if not df_a.empty:
            acc_to_delete = st.selectbox(
                "Select Account to delete",
                df_a["id"].astype(str) + " - " + df_a["code"] + " " + df_a["name"],
            )
            if st.button("Delete Account"):
                aid = acc_to_delete.split(" - ")[0]
                delete_account(company_id, aid)
                st.success("Account deleted.")
                st.experimental_rerun()
        else:
            st.info("No accounts available to delete.")

    # Staff tab
    with tabs[2]:
        st.subheader("Staff")
        staff_list = list_staff(company_id=company_id)
        df_s = pd.DataFrame(staff_list)
        if not df_s.empty:
            st.dataframe(df_s)
        else:
            st.info("No staff found yet.")

        st.markdown("### Add / Update Staff")
        with st.form("staff_form"):
            staff_id = st.text_input("Staff ID (leave blank to create new)")
            name = st.text_input("Staff Name")
            email = st.text_input("Email")
            phone = st.text_input("Phone")
            role = st.text_input("Role / Position")
            submit_s = st.form_submit_button("Save Staff")

        if submit_s:
            if not name:
                st.error("Staff name is required.")
            else:
                upsert_staff(
                    company_id=company_id,
                    staff_id=staff_id.strip() or None,
                    name=name.strip(),
                    email=email.strip(),
                    phone=phone.strip(),
                    role=role.strip(),
                )
                st.success("Staff saved successfully.")
                st.experimental_rerun()

        st.markdown("### Delete Staff")
        if not df_s.empty:
            staff_to_delete = st.selectbox(
                "Select Staff to delete",
                df_s["id"].astype(str) + " - " + df_s["name"],
            )
            if st.button("Delete Staff"):
                sid = staff_to_delete.split(" - ")[0]
                delete_staff(company_id, sid)
                st.success("Staff deleted.")
                st.experimental_rerun()
        else:
            st.info("No staff available to delete.")


# ----------
# Vouchers
# ----------

def app_vouchers():
    require_permission("can_create_voucher")
    user = current_user()
    username = user["username"]
    company_id = user["company_id"]

    # CRM-driven dropdown options
    vendor_options = get_vendor_name_list(company_id)
    requester_options = get_requester_options(company_id)
    account_options = get_expense_asset_account_options(company_id)
    payable_options = get_payable_account_options(company_id)

    st.subheader("Create Voucher")

    vendor = st.selectbox("Vendor (from CRM)", vendor_options)
    requester = st.selectbox("Requester (Staff in CRM)", requester_options)

    # Link vouchers to invoices for this vendor
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

    selected_invoice = None
    if invoice_ref:
        for inv in all_invoices:
            if inv.get("invoice_number") == invoice_ref:
                selected_invoice = inv
                break

    # -----------------------------
    # Invoice Allocation Summary
    # -----------------------------
    invoice_currency = None
    allocation = None
    line_allocations = []

    if selected_invoice:
        try:
            invoice_currency = (selected_invoice.get("currency") or "NGN").upper()
            # Get aggregate allocations from helper
            allocation = get_invoice_allocations(company_id, invoice_ref)
            # Get any existing voucher lines for this invoice
            line_allocations = list_voucher_lines_for_invoice(company_id, invoice_ref)
        except Exception as e:
            st.warning(f"Could not load invoice allocations: {e}")
            allocation = None

    # Balances we’ll use for validation
    actual_balance = None
    vat_balance = None
    wht_balance = None

    # ---- Invoice allocation summary (amounts, paid, balances) ----
    if selected_invoice is not None and allocation is not None:
        inv_vatable = float(selected_invoice.get("vatable_amount") or 0.0)
        inv_non_vatable = float(selected_invoice.get("non_vatable_amount") or 0.0)
        inv_vat_total = float(selected_invoice.get("vat_amount") or 0.0)
        inv_wht_total = float(selected_invoice.get("wht_amount") or 0.0)

        # Base (invoice) amount = vatable + non-vatable
        actual_total = inv_vatable + inv_non_vatable

        amount_paid = float(allocation.get("amount_paid") or 0.0)
        vat_paid = float(allocation.get("vat_paid") or 0.0)
        wht_paid = float(allocation.get("wht_paid") or 0.0)

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
        with c3:
            st.markdown("**WHT Allocation**")
            st.write(f"Invoice WHT: **{inv_wht_total:,.2f} {cur_code}**")
            st.markdown(
                "WHT Deducted via Vouchers: "
                f"<span style='color: green; font-weight:bold;'>{wht_paid:,.2f} {cur_code}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "WHT Balance: "
                f"<span style='color: red; font-weight:bold;'>{wht_balance:,.2f} {cur_code}</span>",
                unsafe_allow_html=True,
            )

        # Detailed voucher lines (if any)
        st.markdown("#### Detailed Voucher Line Allocations")
        if line_allocations:
            df_lines = pd.DataFrame(line_allocations)
            st.dataframe(df_lines)
        else:
            st.info("No voucher lines found yet for this invoice.")

        st.markdown("---")

    # Determine default currency based on invoice or fallback
    currencies = ["NGN", "USD", "GBP", "EUR"]
    default_index = 0
    if invoice_currency in currencies:
        default_index = currencies.index(invoice_currency)

    currency = st.selectbox(
        "Currency",
        currencies,
        index=default_index,
        help="Auto-fills from the selected invoice if available, but you can override.",
    )

    voucher_date = st.date_input("Voucher Date")

    st.markdown("### Voucher Amounts")
    st.caption("Enter at least one of the amounts; the rest can be zero if not applicable.")

    amount = st.number_input("Base Amount", min_value=0.0, format="%.2f")
    vat_amount = st.number_input("VAT Amount", min_value=0.0, format="%.2f")
    wht_amount = st.number_input("WHT Amount", min_value=0.0, format="%.2f")

    expense_account_code = st.selectbox(
        "Expense/Asset Account (from CoA)",
        account_options,
    )
    payable_account_code = st.selectbox(
        "Payable Account (from CoA)",
        payable_options,
    )

    description = st.text_area("Description / Narration")
    bank_details = st.text_input("Bank Details (e.g. Bank Name - Account No.)")

    # Attachment upload
    uploaded = st.file_uploader(
        "Attach supporting document (optional)", type=["pdf", "jpg", "png"]
    )
    file_name = None
    file_bytes = None
    if uploaded is not None:
        file_name = uploaded.name
        file_bytes = uploaded.read()

    # ---------------------------
    # Validation vs invoice
    # ---------------------------
    validation_errors = []
    if selected_invoice is not None and allocation is not None:
        inv_vatable = float(selected_invoice.get("vatable_amount") or 0.0)
        inv_non_vatable = float(selected_invoice.get("non_vatable_amount") or 0.0)
        inv_vat_total = float(selected_invoice.get("vat_amount") or 0.0)
        inv_wht_total = float(selected_invoice.get("wht_amount") or 0.0)

        actual_total = inv_vatable + inv_non_vatable
        amount_paid = float(allocation.get("amount_paid") or 0.0)
        vat_paid = float(allocation.get("vat_paid") or 0.0)
        wht_paid = float(allocation.get("wht_paid") or 0.0)

        actual_balance = actual_total - amount_paid
        vat_balance = inv_vat_total - vat_paid
        wht_balance = inv_wht_total - wht_paid

        # If user enters more than the available balance, pre-warn
        if amount > actual_balance + 1e-6:
            validation_errors.append(
                f"Base amount ({amount:,.2f}) is higher than the invoice base balance ({actual_balance:,.2f})."
            )
        if vat_amount > vat_balance + 1e-6:
            validation_errors.append(
                f"VAT amount ({vat_amount:,.2f}) is higher than the invoice VAT balance ({vat_balance:,.2f})."
            )
        if wht_amount > wht_balance + 1e-6:
            validation_errors.append(
                f"WHT amount ({wht_amount:,.2f}) is higher than the invoice WHT balance ({wht_balance:,.2f})."
            )

    if validation_errors:
        st.markdown("### Allocation Warnings")
        for err_msg in validation_errors:
            st.error(err_msg)

    # ---- Save button (only actually saves if validation passes) ----
    save_clicked = st.button("Save Voucher")

    if save_clicked:
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
                description=description,
                bank_details=bank_details,
                amount=amount,
                vat_amount=vat_amount,
                wht_amount=wht_amount,
                currency=currency,
                expense_account_code=expense_account_code,
                payable_account_code=payable_account_code,
                voucher_date=voucher_date,
            )
            if err is not None:
                st.error(err)
            else:
                st.success("Voucher created successfully.")

                # Save attachment if any
                if file_name and file_bytes and invoice_ref:
                    try:
                        save_voucher_attachment(
                            company_id=company_id,
                            invoice_ref=invoice_ref,
                            file_name=file_name,
                            file_bytes=file_bytes,
                        )
                        st.success("Attachment uploaded successfully.")
                    except Exception as e:
                        st.error(f"Failed to save attachment: {e}")

                st.experimental_rerun()

    st.markdown("## Existing Vouchers")

    vouchers = list_vouchers(company_id=company_id)
    df_vch = pd.DataFrame(vouchers)
    if df_vch.empty:
        st.info("No vouchers found.")
        return

    st.dataframe(df_vch)

    # Status change block
    st.markdown("### Update Voucher Status")
    vch_ids = df_vch["voucher_id"].tolist()
    if vch_ids:
        selected_id = st.selectbox("Select Voucher ID", vch_ids)
        new_status = st.selectbox(
            "New Status", ["PENDING", "APPROVED", "REJECTED", "PAID"]
        )
        if st.button("Change Status"):
            change_voucher_status(company_id, selected_id, new_status)
            st.success("Status updated.")
            st.experimental_rerun()

    # Delete voucher
    st.markdown("### Delete Voucher")
    vch_to_delete = st.selectbox("Select Voucher to delete", vch_ids)
    if st.button("Delete Voucher"):
        delete_voucher(company_id, vch_to_delete)
        st.success("Voucher deleted.")
        st.experimental_rerun()

    # PDF download for voucher
    st.markdown("### Download Voucher PDF")
    pdf_voucher_id = st.selectbox(
        "Select Voucher ID for PDF", vch_ids, key="pdf_voucher_id"
    )
    if st.button("Download Voucher PDF"):
        try:
            pdf_bytes = build_voucher_pdf_bytes(
                company_id=company_id, voucher_id=int(pdf_voucher_id)
            )
        except Exception as e:
            st.error(f"Error generating PDF: {e}")
        else:
            st.download_button(
                label="Download PDF",
                data=pdf_bytes,
                file_name=f"voucher_{pdf_voucher_id}.pdf",
                mime="application/pdf",
            )

    # List attachments for the selected invoice (if any)
    if invoice_ref:
        st.markdown("### Attachments for Selected Invoice")
        try:
            attachments = get_voucher_attachments(company_id, invoice_ref)
            if attachments:
                df_att = pd.DataFrame(attachments)
                st.dataframe(df_att)
            else:
                st.info("No attachments found for this invoice.")
        except Exception as e:
            st.error(f"Error loading attachments: {e}")


# ----------
# Invoices
# ----------

def app_invoices():
    require_permission("can_manage_invoices")
    user = current_user()
    company_id = user["company_id"]

    st.title("Invoices")

    st.markdown("### Create / Update Invoice")

    all_invoices = list_invoices(company_id=company_id)
    df_inv = pd.DataFrame(all_invoices)

    edit_mode = st.checkbox("Edit existing invoice?")
    invoice_number = None
    selected_invoice = None

    if edit_mode and not df_inv.empty:
        inv_choice = st.selectbox("Select Invoice to edit", df_inv["invoice_number"])
        selected_invoice = (
            df_inv[df_inv["invoice_number"] == inv_choice].iloc[0].to_dict()
        )
        invoice_number = selected_invoice["invoice_number"]
    else:
        invoice_number = st.text_input("Invoice Number (leave blank if editing)")

    vendor_options = get_vendor_name_list(company_id)
    if selected_invoice and selected_invoice.get("vendor") in vendor_options:
        vendor_index = vendor_options.index(selected_invoice["vendor"])
    else:
        vendor_index = 0

    vendor = st.selectbox(
        "Vendor",
        vendor_options,
        index=vendor_index,
    )

    currency_options = ["NGN", "USD", "GBP", "EUR"]
    if selected_invoice and selected_invoice.get("currency") in currency_options:
        currency_index = currency_options.index(selected_invoice["currency"])
    else:
        currency_index = 0

    currency = st.selectbox("Currency", currency_options, index=currency_index)

    inv_date = st.date_input(
        "Invoice Date",
        value=selected_invoice["invoice_date"] if selected_invoice else None,
    )
    due_date = st.date_input(
        "Due Date",
        value=selected_invoice["due_date"] if selected_invoice else None,
    )

    vatable_amount = st.number_input(
        "Vatable Amount",
        min_value=0.0,
        format="%.2f",
        value=float(selected_invoice["vatable_amount"])
        if selected_invoice and selected_invoice.get("vatable_amount")
        else 0.0,
    )
    non_vatable_amount = st.number_input(
        "Non-vatable Amount",
        min_value=0.0,
        format="%.2f",
        value=float(selected_invoice["non_vatable_amount"])
        if selected_invoice and selected_invoice.get("non_vatable_amount")
        else 0.0,
    )
    vat_amount = st.number_input(
        "VAT Amount",
        min_value=0.0,
        format="%.2f",
        value=float(selected_invoice["vat_amount"])
        if selected_invoice and selected_invoice.get("vat_amount")
        else 0.0,
    )
    wht_amount = st.number_input(
        "WHT Amount",
        min_value=0.0,
        format="%.2f",
        value=float(selected_invoice["wht_amount"])
        if selected_invoice and selected_invoice.get("wht_amount")
        else 0.0,
    )
    remarks = st.text_area(
        "Remarks",
        value=selected_invoice["remarks"] if selected_invoice else "",
    )

    if st.button("Save Invoice"):
        if not vendor:
            st.error("Vendor is required.")
        else:
            if edit_mode and selected_invoice:
                update_invoice(
                    company_id=company_id,
                    invoice_number=invoice_number,
                    vendor=vendor,
                    currency=currency,
                    invoice_date=inv_date,
                    due_date=due_date,
                    vatable_amount=vatable_amount,
                    non_vatable_amount=non_vatable_amount,
                    vat_amount=vat_amount,
                    wht_amount=wht_amount,
                    remarks=remarks,
                )
                st.success("Invoice updated.")
            else:
                if not invoice_number:
                    st.error("Invoice number is required for new invoices.")
                else:
                    create_invoice(
                        company_id=company_id,
                        invoice_number=invoice_number,
                        vendor=vendor,
                        currency=currency,
                        invoice_date=inv_date,
                        due_date=due_date,
                        vatable_amount=vatable_amount,
                        non_vatable_amount=non_vatable_amount,
                        vat_amount=vat_amount,
                        wht_amount=wht_amount,
                        remarks=remarks,
                    )
                    st.success("Invoice created.")
            st.experimental_rerun()

    st.markdown("### Existing Invoices")
    if not df_inv.empty:
        st.dataframe(df_inv)
    else:
        st.info("No invoices found.")

    st.markdown("### Delete Invoice")
    if not df_inv.empty:
        inv_to_delete = st.selectbox("Select Invoice to delete", df_inv["invoice_number"])
        if st.button("Delete Invoice"):
            delete_invoice(company_id, inv_to_delete)
            st.success("Invoice deleted.")
            st.experimental_rerun()
    else:
        st.info("No invoices available to delete.")


# ----------
# User & Role Management
# ----------

def app_user_admin():
    require_admin()
    user = current_user()
    company_id = user["company_id"]

    st.title("User & Role Management")

    st.markdown("### Existing Users")
    users = list_users(company_id=company_id)
    df_u = pd.DataFrame(users)
    if not df_u.empty:
        st.dataframe(df_u)
    else:
        st.info("No users found.")

    st.markdown("### Update Permissions")
    if not df_u.empty:
        selected_username = st.selectbox(
            "Select user",
            df_u["username"],
        )
        if selected_username:
            perms = {
                "can_manage_crm": st.checkbox("Can manage CRM?"),
                "can_create_voucher": st.checkbox("Can create vouchers?"),
                "can_manage_invoices": st.checkbox("Can manage invoices?"),
                "is_admin": st.checkbox("Is admin?"),
            }
            if st.button("Update Permissions"):
                update_user_permissions(company_id, selected_username, perms)
                st.success("Permissions updated.")
                st.experimental_rerun()

    st.markdown("### Create New User")
    with st.form("create_user_form"):
        new_username = st.text_input("New Username")
        new_password = st.text_input("Password", type="password")
        new_fullname = st.text_input("Full Name")
        perms = {
            "can_manage_crm": st.checkbox("Can manage CRM?", key="perm_crm"),
            "can_create_voucher": st.checkbox(
                "Can create vouchers?", key="perm_voucher"
            ),
            "can_manage_invoices": st.checkbox(
                "Can manage invoices?", key="perm_inv"
            ),
            "is_admin": st.checkbox("Is admin?", key="perm_admin"),
        }
        submit_new = st.form_submit_button("Create User")

    if submit_new:
        if not new_username or not new_password:
            st.error("Username and password are required.")
        else:
            create_user_for_company(
                company_id=company_id,
                username=new_username.strip(),
                password=new_password,
                full_name=new_fullname.strip(),
                permissions=perms,
            )
            st.success("User created successfully.")
            st.experimental_rerun()


# ----------
# Main App Entry Point
# ----------

def main():
    st.set_page_config(page_title="Finance App (Multi-tenant)", layout="wide")

    # Initialize schema & auth
    init_schema()
    init_auth()

    # Login gate
    require_login()
    user = current_user()
    if not user:
        st.stop()

    company_id = user["company_id"]
    username = user["username"]

    st.sidebar.markdown(f"**Logged in as:** {username} (Company: {company_id})")
    logout_button()

    menu = ["Vouchers", "Invoices", "CRM & Master Data", "User Admin"]
    choice = st.sidebar.radio("Go to", menu)

    if choice == "Vouchers":
        app_vouchers()
    elif choice == "Invoices":
        app_invoices()
    elif choice == "CRM & Master Data":
        app_crm()
    elif choice == "User Admin":
        app_user_admin()


if __name__ == "__main__":
    main()
