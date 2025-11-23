# app_main.py
# Main Streamlit app wiring all modules together, with multi-tenant support

import streamlit as st
import pandas as pd
import psycopg2
from typing import List, Dict

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
    list_staff,
    upsert_staff,
    delete_staff,
    delete_account,
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
    get_voucher,
    list_voucher_lines,
    update_voucher,
)
from invoices_module import (
    list_invoices,
    create_invoice,
    update_invoice,
    delete_invoice,
)
from pdf_utils import build_voucher_pdf_bytes


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

    tab_create, tab_edit, tab_list = st.tabs(
        ["Create Voucher", "Edit Voucher", "Voucher List"]
    )

    # ---------- TAB 1: CREATE ----------
    with tab_create:
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

            # Sum voucher_lines for vouchers referencing this invoice
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

            with c3:
                st.markdown("**WHT Allocation**")
                st.write(f"Invoice WHT: **{inv_wht_total:,.2f} {cur_code}**")
                st.markmarkdown if invoice_currency else st.markdown
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

            st.markdown("---")

        # Determine default currency based on selected invoice (if any)
        base_currencies = ["NGN", "USD", "GBP", "EUR"]
        currencies = base_currencies.copy()
        if invoice_currency and invoice_currency not in currencies:
            currencies.append(invoice_currency)

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
        lines: List[Dict] = []
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
                    "amount": amt,
                    "account_name": acct,
                    "vat_percent": vat,
                    "wht_percent": wht,
                }
            )

        # Pre-calc totals for validation
        total_line_amount = sum((l.get("amount") or 0.0) for l in lines)
        total_line_vat = sum(
            round((l.get("amount") or 0.0) * (l.get("vat_percent") or 0.0) / 100.0, 2)
            for l in lines
        )
        total_line_wht = sum(
            round((l.get("amount") or 0.0) * (l.get("wht_percent") or 0.0) / 100.0, 2)
            for l in lines
        )

        validation_errors = []
        if selected_invoice is not None:
            cur_code = invoice_currency or "NGN"

            if actual_balance is not None and total_line_amount > actual_balance + 0.0001:
                validation_errors.append(
                    f"Total Amount in voucher lines ({total_line_amount:,.2f} {cur_code}) "
                    f"is greater than the Base Balance to Pay ({actual_balance:,.2f} {cur_code})."
                )

            if vat_balance is not None and total_line_vat > vat_balance + 0.0001:
                validation_errors.append(
                    f"Total VAT amount in voucher lines ({total_line_vat:,.2f} {cur_code}) "
                    f"is greater than the VAT Balance ({vat_balance:,.2f} {cur_code})."
                )

            if wht_balance is not None and total_line_wht > wht_balance + 0.0001:
                validation_errors.append(
                    f"Total WHT amount in voucher lines ({total_line_wht:,.2f} {cur_code}) "
                    f"is greater than the WHT Balance ({wht_balance:,.2f} {cur_code})."
                )

        if validation_errors:
            for msg in validation_errors:
                st.error(msg)

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

    # Pre-load vouchers once for other tabs
    vdf = pd.DataFrame(list_vouchers(company_id=company_id))

    # ---------- TAB 2: EDIT ----------
    with tab_edit:
        st.subheader("Edit Voucher")

        if vdf.empty:
            st.info("No vouchers yet.")
        else:
            display_cols = [c for c in vdf.columns if c not in ("file_data",)]
            st.dataframe(vdf[display_cols])

            voucher_ids = vdf["id"].tolist()
            edit_vid = st.selectbox(
                "Select Voucher ID to edit",
                voucher_ids,
                format_func=lambda x: f"ID {x}",
                key="edit_voucher_id",
            )

            if edit_vid:
                header = get_voucher(company_id=company_id, voucher_id=int(edit_vid))
                existing_lines = list_voucher_lines(
                    company_id=company_id, voucher_id=int(edit_vid)
                )

                with st.form("edit_voucher_form"):
                    st.markdown("**Voucher Header**")
                    ev_vendor = st.selectbox(
                        "Vendor (from CRM)",
                        vendor_options,
                        index=vendor_options.index(header["vendor"])
                        if header["vendor"] in vendor_options
                        else 0,
                        key="ev_vendor",
                    )
                    ev_requester = st.selectbox(
                        "Requester (Staff in CRM)",
                        requester_options,
                        index=requester_options.index(header["requester"])
                        if header["requester"] in requester_options
                        else 0,
                        key="ev_requester",
                    )
                    ev_invoice_ref = st.text_input(
                        "Invoice / Reference",
                        value=header.get("invoice_ref") or "",
                        key="ev_invoice_ref",
                    )

                    base_currencies_edit = ["NGN", "USD", "GBP", "EUR"]
                    currencies_edit = base_currencies_edit.copy()
                    if header["currency"] not in currencies_edit:
                        currencies_edit.append(header["currency"])

                    ev_currency = st.selectbox(
                        "Currency",
                        currencies_edit,
                        index=currencies_edit.index(header["currency"]),
                        key="ev_currency",
                    )

                    ev_uploaded = st.file_uploader(
                        "Replace supporting document (optional)",
                        type=["pdf", "jpg", "png"],
                        key="ev_voucher_file",
                    )
                    ev_file_name = None
                    ev_file_bytes = None
                    if ev_uploaded is not None:
                        ev_file_name = ev_uploaded.name
                        ev_file_bytes = ev_uploaded.read()

                    st.markdown("**Voucher Lines (Edit)**")
                    ev_lines: List[Dict] = []
                    num_ev_lines = st.number_input(
                        "Number of lines (edit mode)",
                        min_value=1,
                        max_value=20,
                        value=max(1, len(existing_lines)),
                        step=1,
                        key="ev_num_lines",
                    )

                    for i in range(int(num_ev_lines)):
                        st.markdown(f"**Line {i+1}**")
                        col1, col2, col3, col4, col5 = st.columns([3, 1, 2, 1, 1])

                        existing = (
                            existing_lines[i] if i < len(existing_lines) else {}
                        )
                        with col1:
                            desc = st.text_input(
                                "Description",
                                value=existing.get("description") or "",
                                key=f"ev_line_desc_{i}",
                            )
                        with col2:
                            amt = st.number_input(
                                "Amount",
                                min_value=0.0,
                                step=0.01,
                                value=float(existing.get("amount") or 0.0),
                                key=f"ev_line_amt_{i}",
                            )
                        with col3:
                            acct = st.selectbox(
                                "Expense / Asset Account (Chart of Accounts)",
                                account_options,
                                index=account_options.index(
                                    existing.get("account_name")
                                )
                                if existing.get("account_name") in account_options
                                else 0,
                                key=f"ev_line_acct_{i}",
                            )
                        with col4:
                            vat = st.number_input(
                                "VAT %",
                                min_value=0.0,
                                step=0.5,
                                value=float(existing.get("vat_percent") or 0.0),
                                key=f"ev_line_vat_{i}",
                            )
                        with col5:
                            wht = st.number_input(
                                "WHT %",
                                min_value=0.0,
                                step=0.5,
                                value=float(existing.get("wht_percent") or 0.0),
                                key=f"ev_line_wht_{i}",
                            )

                        ev_lines.append(
                            {
                                "description": desc,
                                "amount": amt,
                                "account_name": acct,
                                "vat_percent": vat,
                                "wht_percent": wht,
                            }
                        )

                    col_ev1, col_ev2 = st.columns(2)
                    with col_ev1:
                        save_edit = st.form_submit_button("Save Voucher Changes")
                    with col_ev2:
                        delete_edit = st.form_submit_button("Delete This Voucher")

                    if save_edit:
                        err = update_voucher(
                            company_id=company_id,
                            voucher_id=int(edit_vid),
                            username=username,
                            vendor=ev_vendor,
                            requester=ev_requester,
                            invoice_ref=ev_invoice_ref,
                            currency=ev_currency,
                            lines=ev_lines,
                            file_name=ev_file_name,
                            file_bytes=ev_file_bytes,
                        )
                        if err:
                            st.error(err)
                        else:
                            st.success("Voucher updated successfully.")
                            st.experimental_rerun()

                    if delete_edit:
                        err = delete_voucher(
                            company_id=company_id,
                            voucher_id=int(edit_vid),
                            actor_username=username,
                        )
                        if err:
                            st.error(err)
                        else:
                            st.success("Voucher deleted.")
                            st.experimental_rerun()

        st.markdown("---")
        st.markdown("### Update Voucher Status")

        col1, col2, col3 = st.columns(3)
        with col1:
            selected_id = st.number_input(
                "Voucher ID",
                min_value=0,
                step=1,
                value=0,
                help="Enter the voucher ID you want to act on.",
                key="status_voucher_id",
            )
        with col2:
            action = st.selectbox(
                "Action",
                ["--", "Submit for approval", "Mark as draft", "Approve", "Reject"],
            )
        with col3:
            st.write(" ")

        if st.button("Apply Action on Voucher", key="apply_status_btn"):
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

        st.markdown("### Export Voucher to PDF")
        pdf_id = st.number_input(
            "Voucher ID to export",
            min_value=0,
            step=1,
            value=0,
            key="pdf_voucher_id",
        )
        if pdf_id > 0 and st.button("Download Voucher PDF"):
            try:
                pdf_bytes = build_voucher_pdf_bytes(
                    company_id=company_id, voucher_id=int(pdf_id)
                )
            except Exception as e:
                st.error(f"Error generating PDF: {e}")
            else:
                st.download_button(
                    label="Download PDF",
                    data=pdf_bytes,
                    file_name=f"voucher_{pdf_id}.pdf",
                    mime="application/pdf",
                )

    # ---------- TAB 3: LIST ----------
    with tab_list:
        st.subheader("Voucher List")
        if vdf.empty:
            st.info("No vouchers yet.")
        else:
            display_cols = [c for c in vdf.columns if c not in ("file_data",)]
            st.dataframe(vdf[display_cols])


# -------------------
# Invoices
# -------------------

def app_invoices():
    require_permission("can_create_voucher")
    user = current_user()
    username = user["username"]
    company_id = user["company_id"]

    vendor_options = get_vendor_name_list(company_id)
    payable_options = get_payable_account_options(company_id)
    expense_asset_options = get_expense_asset_account_options(company_id)

    tab_create, tab_edit, tab_list = st.tabs(
        ["Create Invoice", "Edit Invoice", "Invoice List"]
    )

    # ---------- TAB 1: CREATE ----------
    with tab_create:
        st.subheader("Create Invoice")

        st.info(
            "Invoice number will be auto-generated using date and time when you save."
        )

        vendor = st.selectbox("Vendor (from CRM)", vendor_options)
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
            try:
                _ = create_invoice(
                    company_id=company_id,
                    username=username,
                    invoice_number="",  # let backend auto-generate
                    vendor_invoice_number=vendor_invoice_number,
                    vendor=vendor,
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
                st.error(str(ex))
            else:
                st.success("Invoice created successfully.")
                st.experimental_rerun()

    # Pre-load invoices for edit/list tabs
    idf = pd.DataFrame(list_invoices(company_id=company_id))

    # ---------- TAB 2: EDIT ----------
    with tab_edit:
        st.subheader("Edit Invoice")

        if idf.empty:
            st.info("No invoices yet.")
        else:
            st.dataframe(idf)

            invoice_ids = idf["id"].tolist()
            edit_iid = st.selectbox(
                "Select Invoice ID to edit",
                invoice_ids,
                format_func=lambda x: f"ID {x}",
                key="edit_invoice_id",
            )

            if edit_iid:
                row = idf[idf["id"] == edit_iid].iloc[0].to_dict()

                with st.form("edit_invoice_form"):
                    ev_vendor = st.selectbox(
                        "Vendor (from CRM)",
                        vendor_options,
                        index=vendor_options.index(row["vendor"])
                        if row["vendor"] in vendor_options
                        else 0,
                        key="ei_vendor",
                    )
                    ev_vendor_inv_no = st.text_input(
                        "Vendor Invoice Number",
                        value=row.get("vendor_invoice_number") or "",
                        key="ei_vendor_inv_no",
                    )
                    ev_summary = st.text_area(
                        "Summary",
                        value=row.get("summary") or "",
                        key="ei_summary",
                    )

                    ev_vatable_amount = st.number_input(
                        "Vatable Amount",
                        min_value=0.0,
                        step=0.01,
                        value=float(row.get("vatable_amount") or 0.0),
                        key="ei_vatable",
                    )
                    ev_vat_rate = st.number_input(
                        "VAT Rate (%)",
                        min_value=0.0,
                        step=0.5,
                        value=float(row.get("vat_rate") or 0.0),
                        key="ei_vat_rate",
                    )
                    ev_wht_rate = st.number_input(
                        "WHT Rate (%)",
                        min_value=0.0,
                        step=0.5,
                        value=float(row.get("wht_rate") or 0.0),
                        key="ei_wht_rate",
                    )
                    ev_non_vatable = st.number_input(
                        "Non-vatable Amount",
                        min_value=0.0,
                        step=0.01,
                        value=float(row.get("non_vatable_amount") or 0.0),
                        key="ei_non_vatable",
                    )

                    ev_terms = st.text_area(
                        "Terms",
                        value=row.get("terms") or "",
                        key="ei_terms",
                    )

                    base_currencies_edit = ["NGN", "USD", "GBP", "EUR"]
                    currencies_edit = base_currencies_edit.copy()
                    if row.get("currency") not in currencies_edit:
                        currencies_edit.append(row.get("currency"))

                    ev_currency = st.selectbox(
                        "Currency",
                        currencies_edit,
                        index=currencies_edit.index(row.get("currency")),
                        key="ei_currency",
                    )

                    ev_payable_account = st.selectbox(
                        "Payable Account (Chart of Accounts)",
                        payable_options,
                        index=payable_options.index(row.get("payable_account"))
                        if row.get("payable_account") in payable_options
                        else 0,
                        key="ei_payable",
                    )
                    ev_expense_asset = st.selectbox(
                        "Expense / Asset Account (Chart of Accounts)",
                        expense_asset_options,
                        index=expense_asset_options.index(
                            row.get("expense_asset_account")
                        )
                        if row.get("expense_asset_account") in expense_asset_options
                        else 0,
                        key="ei_expense_asset",
                    )

                    ev_uploaded = st.file_uploader(
                        "Replace invoice document (optional)",
                        type=["pdf", "jpg", "png"],
                        key="ei_file",
                    )
                    ev_file_name = None
                    ev_file_bytes = None
                    if ev_uploaded is not None:
                        ev_file_name = ev_uploaded.name
                        ev_file_bytes = ev_uploaded.read()

                    col_ei1, col_ei2 = st.columns(2)
                    with col_ei1:
                        save_edit = st.form_submit_button("Save Invoice Changes")
                    with col_ei2:
                        delete_edit = st.form_submit_button("Delete This Invoice")

                    if save_edit:
                        err = update_invoice(
                            company_id=company_id,
                            invoice_id=int(edit_iid),
                            vendor_invoice_number=ev_vendor_inv_no,
                            vendor=ev_vendor,
                            summary=ev_summary,
                            vatable_amount=ev_vatable_amount,
                            vat_rate=ev_vat_rate,
                            wht_rate=ev_wht_rate,
                            non_vatable_amount=ev_non_vatable,
                            terms=ev_terms,
                            payable_account=ev_payable_account,
                            expense_asset_account=ev_expense_asset,
                            currency=ev_currency,
                            username=username,
                            file_name=ev_file_name,
                            file_data=ev_file_bytes,
                        )
                        if err:
                            st.error(err)
                        else:
                            st.success("Invoice updated successfully.")
                            st.experimental_rerun()

                    if delete_edit:
                        err = delete_invoice(
                            company_id=company_id,
                            invoice_id=int(edit_iid),
                            username=username,
                        )
                        if err:
                            st.error(err)
                        else:
                            st.success("Invoice deleted.")
                            st.experimental_rerun()

    # ---------- TAB 3: LIST ----------
    with tab_list:
        st.subheader("Invoice List")
        if idf.empty:
            st.info("No invoices yet.")
        else:
            if "total_amount" in idf.columns:
                idf["total_amount_fmt"] = idf["total_amount"].apply(
                    lambda v: f"{v:,.2f}" if v is not None else ""
                )
            st.dataframe(idf)


# -------------------
# CRM (Staff, Vendors & Accounts)
# -------------------

def app_crm():
    require_permission("can_create_voucher")
    user = current_user()
    username = user["username"]
    company_id = user["company_id"]

    tab_staff, tab_vendors, tab_accounts = st.tabs(
        ["Staff", "Vendors", "Accounts"]
    )

    # -------- Staff Tab --------
    with tab_staff:
        st.subheader("Staff")

        with st.form("staff_form"):
            col1, col2 = st.columns(2)
            with col1:
                first_name = st.text_input("First Name")
                email = st.text_input("Email")
                phone = st.text_input("Phone")
            with col2:
                last_name = st.text_input("Last Name")
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
                    st.experimental_rerun()

        staff_rows = list_staff(company_id=company_id)
        if staff_rows:
            sdf = pd.DataFrame(staff_rows)
            st.dataframe(sdf)

            with st.expander("Edit / Delete Staff"):
                staff_ids = sdf["id"].tolist()
                selected_staff_id = st.selectbox(
                    "Select Staff",
                    staff_ids,
                    format_func=lambda sid: f"{sid} - {sdf[sdf['id'] == sid].iloc[0]['first_name']} {sdf[sdf['id'] == sid].iloc[0]['last_name']}",
                    key="edit_staff_id",
                )
                if selected_staff_id:
                    row = sdf[sdf["id"] == selected_staff_id].iloc[0].to_dict()
                    with st.form("edit_staff_form"):
                        es_first = st.text_input(
                            "First Name", value=row["first_name"]
                        )
                        es_last = st.text_input("Last Name", value=row["last_name"])
                        es_email = st.text_input(
                            "Email", value=row.get("email") or ""
                        )
                        es_phone = st.text_input(
                            "Phone", value=row.get("phone") or ""
                        )
                        es_status = st.selectbox(
                            "Status",
                            ["Active", "Inactive"],
                            index=0
                            if (row.get("status") or "Active") == "Active"
                            else 1,
                        )
                        es_position = st.text_input(
                            "Position / Role", value=row.get("position") or ""
                        )
                        col_s1, col_s2 = st.columns(2)
                        with col_s1:
                            save_staff = st.form_submit_button("Save Staff Changes")
                        with col_s2:
                            delete_staff_btn = st.form_submit_button("Delete Staff")

                        if save_staff:
                            err = upsert_staff(
                                company_id=company_id,
                                staff_id=int(selected_staff_id),
                                first_name=es_first,
                                last_name=es_last,
                                email=es_email,
                                phone=es_phone,
                                status=es_status,
                                position=es_position,
                            )
                            if err:
                                st.error(err)
                            else:
                                st.success("Staff updated.")
                                st.experimental_rerun()

                        if delete_staff_btn:
                            err = delete_staff(
                                company_id=company_id,
                                staff_id=int(selected_staff_id),
                                username=username,
                            )
                            if err:
                                st.error(err)
                            else:
                                st.success("Staff deleted.")
                                st.experimental_rerun()
        else:
            st.info("No staff yet.")

    # -------- Vendors Tab --------
    with tab_vendors:
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
                    upsert_vendor(
                        company_id=company_id,
                        name=name,
                        contact_person=contact,
                        bank_name=bank_name,
                        bank_account=bank_account,
                        notes=notes,
                        username=username,
                    )
                    st.success("Vendor saved.")
                    st.experimental_rerun()

        vdf = pd.DataFrame(list_vendors(company_id=company_id))
        if not vdf.empty:
            st.dataframe(vdf)

            with st.expander("Edit / Delete Vendor"):
                vendor_ids = vdf["id"].tolist()
                selected_vid = st.selectbox(
                    "Select Vendor",
                    vendor_ids,
                    format_func=lambda vid: f"{vid} - {vdf[vdf['id'] == vid].iloc[0]['name']}",
                    key="edit_vendor_id",
                )
                if selected_vid:
                    row = vdf[vdf["id"] == selected_vid].iloc[0].to_dict()
                    with st.form("edit_vendor_form"):
                        ev_name = st.text_input("Vendor Name", value=row["name"])
                        ev_contact = st.text_input(
                            "Contact Person", value=row.get("contact_person") or ""
                        )
                        ev_bank = st.text_input(
                            "Bank Name", value=row.get("bank_name") or ""
                        )
                        ev_bank_acct = st.text_input(
                            "Bank Account", value=row.get("bank_account") or ""
                        )
                        ev_notes = st.text_area("Notes", value=row.get("notes") or "")

                        col_v1, col_v2 = st.columns(2)
                        with col_v1:
                            save_vendor = st.form_submit_button("Save Vendor Changes")
                        with col_v2:
                            delete_vendor_btn = st.form_submit_button("Delete Vendor")

                        if save_vendor:
                            upsert_vendor(
                                company_id=company_id,
                                name=ev_name,
                                contact_person=ev_contact,
                                bank_name=ev_bank,
                                bank_account=ev_bank_acct,
                                notes=ev_notes,
                                username=username,
                            )
                            st.success("Vendor updated.")
                            st.experimental_rerun()

                        if delete_vendor_btn:
                            err = delete_vendor(
                                company_id=company_id,
                                vendor_id=int(selected_vid),
                                username=username,
                            )
                            if err:
                                st.error(err)
                            else:
                                st.success("Vendor deleted.")
                                st.experimental_rerun()
        else:
            st.info("No vendors yet.")

    # -------- Accounts Tab --------
    with tab_accounts:
        st.subheader("Accounts (Chart of Accounts)")

        with st.form("account_form"):
            code = st.text_input("Account Code")
            name = st.text_input("Account Name")
            acc_type = st.selectbox(
                "Type",
                ["Asset", "Liability", "Equity", "Expense", "Income"],
                index=0,
            )
            submitted_account = st.form_submit_button("Save Account")
            if submitted_account:
                if not code or not name:
                    st.error("Code and name are required.")
                else:
                    upsert_account(
                        company_id=company_id,
                        code=code,
                        name=name,
                        account_type=acc_type,
                        username=username,
                    )
                    st.success("Account saved.")
                    st.experimental_rerun()

        all_accounts = list_accounts(company_id=company_id)

        if all_accounts:
            adf = pd.DataFrame(all_accounts)
            st.dataframe(adf)

            with st.expander("Delete Account"):
                acc_codes = adf["code"].tolist()
                selected_code = st.selectbox(
                    "Select Account Code to delete",
                    acc_codes,
                    key="delete_acc_code",
                )
                if st.button("Delete Selected Account"):
                    err = delete_account(
                        company_id=company_id,
                        code=selected_code,
                        username=username,
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success("Account deleted.")
                        st.experimental_rerun()
        else:
            st.info("No accounts yet.")


# -------------------
# Reports
# -------------------

def app_reports():
    require_login()
    user = current_user()
    company_id = user["company_id"]

    st.subheader("Reports")

    tab1, tab2, tab3 = st.tabs(
        ["Voucher Register", "Invoice Register", "CRM / Master Data"]
    )

    with tab1:
        st.markdown("### Voucher Register")
        vdf = pd.DataFrame(list_vouchers(company_id=company_id))
        if vdf.empty:
            st.info("No vouchers yet.")
        else:
            if "vendor" in vdf.columns:
                vendors = ["(All)"] + sorted(
                    [v for v in vdf["vendor"].dropna().unique().tolist()]
                )
                vendor_filter = st.selectbox("Filter by Vendor", vendors)
                if vendor_filter != "(All)":
                    vdf = vdf[vdf["vendor"] == vendor_filter]

            if "status" in vdf.columns:
                statuses = ["(All)"] + sorted(
                    [s for s in vdf["status"].dropna().unique().tolist()]
                )
                status_filter = st.selectbox("Filter by Status", statuses)
                if status_filter != "(All)":
                    vdf = vdf[vdf["status"] == status_filter]

            st.dataframe(vdf)

    with tab2:
        st.markdown("### Invoice Register")
        idf = pd.DataFrame(list_invoices(company_id=company_id))
        if idf.empty:
            st.info("No invoices yet.")
        else:
            if "vendor" in idf.columns:
                vendors = ["(All)"] + sorted(
                    [v for v in idf["vendor"].dropna().unique().tolist()]
                )
                vendor_filter = st.selectbox(
                    "Filter by Vendor", vendors, key="inv_vendor_filter"
                )
                if vendor_filter != "(All)":
                    idf = idf[idf["vendor"] == vendor_filter]

            if "currency" in idf.columns:
                currencies = ["(All)"] + sorted(
                    [c for c in idf["currency"].dropna().unique().tolist()]
                )
                currency_filter = st.selectbox(
                    "Filter by Currency", currencies, key="inv_currency_filter"
                )
                if currency_filter != "(All)":
                    idf = idf[idf["currency"] == currency_filter]

            st.dataframe(idf)

    with tab3:
        st.markdown("### CRM / Master Data")

        st.markdown("#### Vendors")
        vdf = pd.DataFrame(list_vendors(company_id=company_id))
        if vdf.empty:
            st.info("No vendors yet.")
        else:
            st.dataframe(vdf)

        st.markdown("#### Staff")
        sdf = pd.DataFrame(list_staff(company_id=company_id))
        if sdf.empty:
            st.info("No staff yet.")
        else:
            st.dataframe(sdf)

        st.markdown("#### Accounts (Chart of Accounts)")
        adf = pd.DataFrame(list_accounts(company_id=company_id))
        if adf.empty:
            st.info("No accounts yet.")
        else:
            st.dataframe(adf)


# -------------------
# User Management
# -------------------

def app_user_management():
    require_permission("can_manage_users")
    admin = current_user()
    admin_name = admin["username"]
    company_id = admin["company_id"]

    st.subheader(
        f"User Management – {admin['company_name']} ({admin['company_code']})"
    )

    st.markdown("### Create New User")

    with st.form("create_user_form"):
        new_username = st.text_input("Username")
        pw1 = st.text_input("Password", type="password")
        pw2 = st.text_input("Confirm Password", type="password")
        new_role = st.selectbox("Role", ["user", "admin"], index=0)

        col1, col2, col3 = st.columns(3)
        with col1:
            can_create_voucher = st.checkbox(
                "Can create vouchers",
                value=True,
            )
        with col2:
            can_approve_voucher = st.checkbox(
                "Can approve vouchers",
                value=(new_role == "admin"),
            )
        with col3:
            can_manage_users = st.checkbox(
                "Can manage users",
                value=(new_role == "admin"),
            )

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
                    st.experimental_rerun()

    st.markdown("---")
    st.markdown("### Existing Users")

    users = list_users(company_id=company_id)
    if not users:
        st.info("No users found.")
        return

    for u in users:
        with st.expander(
            f"{u['username']} (role: {u['role']})",
            expanded=False,
        ):
            with st.form(f"edit_user_{u['id']}"):
                role = st.selectbox(
                    "Role",
                    ["user", "admin"],
                    index=0 if u["role"] == "user" else 1,
                    key=f"edit_role_{u['id']}",
                )
                col1, col2, col3 = st.columns(3)
                with col1:
                    c_create = st.checkbox(
                        "Can create vouchers",
                        value=bool(u["can_create_voucher"]),
                        key=f"edit_create_{u['id']}",
                    )
                with col2:
                    c_approve = st.checkbox(
                        "Can approve vouchers",
                        value=bool(u["can_approve_voucher"]),
                        key=f"edit_approve_{u['id']}",
                    )
                with col3:
                    c_manage = st.checkbox(
                        "Can manage users",
                        value=bool(u["can_manage_users"]),
                        key=f"edit_manage_{u['id']}",
                    )

                save_btn = st.form_submit_button("Save Changes")
                if save_btn:
                    err = update_user_permissions(
                        actor_username=admin_name,
                        user_id=u["id"],
                        company_id=company_id,
                        role=role,
                        can_create_voucher=c_create,
                        can_approve_voucher=c_approve,
                        can_manage_users=c_manage,
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success("Permissions updated.")
                        st.experimental_rerun()


# -------------------
# Account page
# -------------------

def app_account():
    require_login()
    user = current_user()

    st.subheader("My Account")

    st.markdown(f"**Username:** {user['username']}")
    st.markdown(f"**Role:** {user['role']}")
    st.markdown(f"**Company:** {user['company_name']} ({user['company_code']})")

    st.markdown("---")


# -------------------
# DB Browser (admin)
# -------------------

def app_db_browser():
    require_admin()
    st.subheader("DB Browser (admin only)")

    query = st.text_area(
        "SQL Query", "SELECT * FROM vouchers ORDER BY id DESC LIMIT 50;"
    )
    if st.button("Run Query"):
        try:
            with connect() as conn:
                df = pd.read_sql_query(query, conn)
            st.dataframe(df)
        except psycopg2.Error as e:
            st.error(f"Database error: {e}")
        except Exception as e:
            st.error(f"Error: {e}")


# -------------------
# Main entry
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
        f"<br/>**Company:** {user['company_name']} ({user['company_code']})",
        unsafe_allow_html=True,
    )
    if st.sidebar.button("Logout"):
        st.session_state["user"] = None
        st.rerun()

    menu = ["Vouchers", "Invoices", "CRM", "Reports", "Account"]

    if user.get("can_manage_users", False):
        menu.append("User Management")

    if user["role"] == "admin":
        menu.append("DB Browser")

    choice = st.sidebar.radio("Go to", menu)

    if choice == "Vouchers":
        app_vouchers()
    elif choice == "Invoices":
        app_invoices()
    elif choice == "CRM":
        app_crm()
    elif choice == "Reports":
        app_reports()
    elif choice == "User Management":
        app_user_management()
    elif choice == "DB Browser":
        app_db_browser()
    elif choice == "Account":
        app_account()


if __name__ == "__main__":
    main()
