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
                "amount": amt,
                "account_name": acct,
                "vat_percent": vat,
                "wht_percent": wht,
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

    # Show validation errors immediately so user knows why save won't work
    if validation_errors:
        for msg in validation_errors:
            st.error(msg)

    # ---- Save button (only actually saves if validation passes) ----
    save_clicked = st.button("Save Voucher")

    if save_clicked:
        if validation_errors:
            # Do not call create_voucher – just explain
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

    st.markdown("---")
    st.subheader("Recent Vouchers")

    vdf = pd.DataFrame(list_vouchers(company_id=company_id))
    if not vdf.empty:
        display_cols = [c for c in vdf.columns if c not in ("file_data",)]
        st.dataframe(vdf[display_cols])

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
    else:
        st.info("No vouchers yet.")


# -------------------
# Invoices
# -------------------

def app_invoices():
    require_permission("can_create_voucher")
    user = current_user()
    username = user["username"]
    company_id = user["company_id"]

    # CRM-driven dropdown options
    vendor_options = get_vendor_name_list(company_id)
    payable_options = get_payable_account_options(company_id)
    expense_asset_options = get_expense_asset_account_options(company_id)

    st.subheader("Create Invoice")

    st.info("Invoice number will be auto-generated using date and time when you save.")

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
        err = None
        try:
            # Let backend auto-generate invoice_number if empty
            create_invoice(
                company_id=company_id,
                username=username,
                invoice_number="",  # backend should handle empty as "auto"
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
            err = str(ex)

        if err:
            st.error(err)
        else:
            st.success("Invoice created successfully.")

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
# CRM (Staff, Vendors & Accounts)
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

    vdf = pd.DataFrame(list_vendors(company_id=company_id))
    if not vdf.empty:
        st.dataframe(vdf)
    else:
        st.info("No vendors yet.")

    st.markdown("---")
    # ---- Accounts ----
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

    all_accounts = list_accounts(company_id=company_id)

    payable_accounts = [
        a for a in all_accounts if a.get("type") in ("Liability", "Equity")
    ]
    expense_asset_accounts = [
        a for a in all_accounts if a.get("type") in ("Expense", "Asset")
    ]

    st.markdown("**Payable Accounts (Liability / Equity)**")
    if payable_accounts:
        st.dataframe(pd.DataFrame(payable_accounts))
    else:
        st.info("No payable accounts yet.")

    st.markdown("**Expense & Asset Accounts**")
    if expense_asset_accounts:
        st.dataframe(pd.DataFrame(expense_asset_accounts))
    else:
        st.info("No expense or asset accounts yet.")




# ============================ TAB: VOUCHERS (ALL / EDIT) ====================
from contextlib import closing
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime

import streamlit as st

from auth_module import current_user
from db_config import connect
from vouchers_module import list_vouchers, list_voucher_lines, update_voucher
from invoices_module import list_invoices as list_invoices_for_dropdown
from crm_gateway import (
    get_vendor_name_list,
    get_requester_options,
    get_expense_asset_account_options,
)
from pdf_utils import build_voucher_pdf_bytes

# You already have these helpers in your app_main
# - money(value, currency="NGN")
# - safe_index(lst, value)
# - embed_file(name, bytes_data)
# - REPORTLAB_OK
# - rerun()
# - company_settings: dict with keys like department_default, authorizer_name, approver_name

with tabs[1]:
    st.markdown(
        "<div class='card'><div class='card-header'>📑 All Vouchers</div>",
        unsafe_allow_html=True,
    )

    user = current_user()
    company_id = user["company_id"]
    username = user["username"]

    # Dropdown source data
    vendors = get_vendor_name_list(company_id)
    requesters = get_requester_options(company_id)
    line_accounts = get_expense_asset_account_options(company_id)

    # Invoices for linking
    inv_rows = list_invoices_for_dropdown(company_id)
    invoice_numbers = ["-- None --"] + [r["invoice_number"] for r in inv_rows]

    vouchers = list_vouchers(company_id)

    for v in vouchers:
        voucher_id = int(v["id"])
        lines = list_voucher_lines(company_id, voucher_id)

        # Determine voucher currency from header
        voucher_currency = v.get("currency") or "NGN"

        # Try to infer currency from linked invoice (if any)
        try:
            if v.get("invoice_ref"):
                linked = next(
                    (r for r in inv_rows if r["invoice_number"] == v["invoice_ref"]),
                    None,
                )
                if linked and linked.get("currency"):
                    voucher_currency = linked["currency"]
        except Exception:
            pass

        total_payable_v = 0.0
        for ln in lines:
            amt = float(ln.get("amount") or 0.0)
            vat_val = float(ln.get("vat_value") or 0.0)
            wht_val = float(ln.get("wht_value") or 0.0)
            total_payable_v += (amt + vat_val - wht_val)

        display_vnum = v.get("voucher_number") or f"{voucher_id}"

        with st.expander(
            f"VOUCHER {display_vnum} • {v.get('vendor') or ''} • PAYABLE {money(total_payable_v, voucher_currency)}",
            expanded=False,
        ):
            col1, col2, col3, col4 = st.columns([2, 2, 2, 3])

            # Header fields
            col1.text_input(
                "Voucher Number",
                value=v.get("voucher_number") or "",
                key=f"vnum_{voucher_id}",
                disabled=True,
            )

            new_vendor = col2.selectbox(
                "Vendor",
                vendors,
                index=safe_index(vendors, v.get("vendor")),
                key=f"ev_{voucher_id}",
            )

            new_requester = col3.selectbox(
                "Requester",
                requesters,
                index=safe_index(requesters, v.get("requester")),
                key=f"er_{voucher_id}",
            )

            current_inv = v.get("invoice_ref")
            if current_inv not in invoice_numbers:
                current_inv = "-- None --"

            new_invoice = col4.selectbox(
                "Invoice",
                invoice_numbers,
                index=safe_index(invoice_numbers, current_inv),
                key=f"ei_{voucher_id}",
            )

            invoice_linked_edit = new_invoice != "-- None --"

            st.markdown("### Line Items")
            updated_lines: List[Dict[str, Any]] = []
            edit_total = 0.0

            for idx, ln in enumerate(lines):
                line_id = ln["id"]
                descr_default = ln.get("description") or ""
                amt_default = float(ln.get("amount") or 0.0)
                vat_default = float(ln.get("vat_percent") or 0.0)
                wht_default = float(ln.get("wht_percent") or 0.0)
                acct_default = ln.get("account_name") or ""

                if invoice_linked_edit:
                    lc = st.columns([4, 2, 2, 2])
                    desc = lc[0].text_input(
                        "Desc",
                        descr_default,
                        key=f"ldesc_{voucher_id}_{line_id}",
                    )
                    amt = lc[1].number_input(
                        "Amt",
                        value=amt_default,
                        min_value=0.00,
                        format="%.2f",
                        key=f"lamt_{voucher_id}_{line_id}",
                    )
                    vat = lc[2].number_input(
                        "VAT%",
                        value=vat_default,
                        key=f"lvat_{voucher_id}_{line_id}",
                    )
                    wht = lc[3].number_input(
                        "WHT%",
                        value=wht_default,
                        key=f"lwht_{voucher_id}_{line_id}",
                    )
                    acct_val = acct_default
                else:
                    lc = st.columns([3, 2, 3, 2, 2])
                    desc = lc[0].text_input(
                        "Desc",
                        descr_default,
                        key=f"ldesc_{voucher_id}_{line_id}",
                    )
                    amt = lc[1].number_input(
                        "Amt",
                        value=amt_default,
                        min_value=0.00,
                        format="%.2f",
                        key=f"lamt_{voucher_id}_{line_id}",
                    )
                    acct_val = lc[2].selectbox(
                        "Expense or Asset Account",
                        line_accounts,
                        index=safe_index(line_accounts, acct_default),
                        key=f"lacct_{voucher_id}_{line_id}",
                    )
                    vat = lc[3].number_input(
                        "VAT%",
                        value=vat_default,
                        key=f"lvat_{voucher_id}_{line_id}",
                    )
                    wht = lc[4].number_input(
                        "WHT%",
                        value=wht_default,
                        key=f"lwht_{voucher_id}_{line_id}",
                    )

                vat_val = amt * vat / 100.0
                wht_val = amt * wht / 100.0
                total_val = amt + vat_val - wht_val
                edit_total += float(total_val)

                st.markdown(
                    f"<div class='calc-line'>VAT: {money(vat_val, voucher_currency)} • "
                    f"WHT: {money(wht_val, voucher_currency)} • "
                    f"PAYABLE: {money(total_val, voucher_currency)}</div>",
                    unsafe_allow_html=True,
                )

                updated_lines.append(
                    {
                        "id": line_id,
                        "description": desc,
                        "amount": float(amt),
                        "account_name": acct_val,
                        "vat_percent": float(vat),
                        "wht_percent": float(wht),
                        "vat_value": float(vat_val),
                        "wht_value": float(wht_val),
                        "total": float(total_val),
                    }
                )

            st.markdown(
                f"<div class='micro-note'>EDITED PAYABLE: {money(edit_total, voucher_currency)}</div>",
                unsafe_allow_html=True,
            )

            # ------- Existing attached documents (voucher_documents) -------
            extra_vdocs = []
            try:
                with closing(connect()) as conn, closing(conn.cursor()) as cur2:
                    cur2.execute(
                        """
                        SELECT id, file_name, uploaded_at
                        FROM voucher_documents
                        WHERE company_id = %s
                          AND voucher_id = %s
                        ORDER BY id DESC
                        """,
                        (company_id, voucher_id),
                    )
                    extra_vdocs = cur2.fetchall()
            except Exception:
                extra_vdocs = []

            if extra_vdocs:
                options_docs = ["-- Select attached document to preview --"] + [
                    f"{row[0]} - {row[1]} ({row[2] or ''})" for row in extra_vdocs
                ]
                sel_doc = st.selectbox(
                    "Existing Attached Documents",
                    options_docs,
                    key=f"voucher_doc_select_{voucher_id}",
                )
                if sel_doc != "-- Select attached document to preview --":
                    try:
                        sel_id = int(sel_doc.split(" - ", 1)[0])
                    except Exception:
                        sel_id = None
                    if sel_id is not None:
                        with closing(connect()) as conn, closing(conn.cursor()) as cur2:
                            cur2.execute(
                                "SELECT file_name, file_data FROM voucher_documents WHERE id=%s",
                                (sel_id,),
                            )
                            rowd = cur2.fetchone()
                        if rowd and rowd[0] and rowd[1]:
                            embed_file(rowd[0], rowd[1])

                        if st.button(
                            "Delete Selected Attachment",
                            key=f"del_voucher_doc_{voucher_id}",
                        ):
                            try:
                                with closing(connect()) as conn, closing(
                                    conn.cursor()
                                ) as cur_del:
                                    cur_del.execute(
                                        "DELETE FROM voucher_documents WHERE id = %s",
                                        (sel_id,),
                                    )
                                    conn.commit()
                                # Clear selectbox state
                                select_key = f"voucher_doc_select_{voucher_id}"
                                if select_key in st.session_state:
                                    st.session_state.pop(select_key, None)
                                st.success("Attachment deleted.")
                                rerun()
                            except Exception as e:
                                st.error(f"Failed to delete attachment: {e}")
            else:
                st.caption("No additional attached documents for this voucher yet.")

            # ------- Multi-file uploader (saved into voucher_documents) -------
            new_files = st.file_uploader(
                "Attach Voucher Documents (PDF/JPG/PNG) — accepts multiple files",
                type=["pdf", "jpg", "jpeg", "png"],
                key=f"files_{voucher_id}",
                accept_multiple_files=True,
            )

            new_attachments: List[Tuple[str, bytes]] = []
            if new_files:
                for f in new_files:
                    try:
                        fb = f.read()
                    except Exception:
                        fb = None
                    if not fb:
                        continue
                    new_attachments.append((f.name, fb))

            act_left, act_right = st.columns([1, 1])

            # ------- Save header+lines+new attachments -------
            if act_left.button("Save Changes", key=f"save_{voucher_id}"):
                err = update_voucher(
                    company_id=company_id,
                    username=username,
                    voucher_id=voucher_id,
                    vendor=str(new_vendor),
                    requester=str(new_requester),
                    invoice_ref=(new_invoice if new_invoice != "-- None --" else None),
                    currency=voucher_currency,
                    lines=updated_lines,
                    new_attachments=new_attachments if new_attachments else None,
                )
                if err:
                    st.error(err)
                else:
                    st.success("Voucher updated!")
                    rerun()

            # ------- Download PDF (using pdf_utils + deduped attachments) -------
            if REPORTLAB_OK:
                try:
                    # Base PDF from voucher + lines
                    base_pdf_bytes = build_voucher_pdf_bytes(company_id, voucher_id)

                    # (Optional) if you want to keep the attachment-merging logic you had
                    # you can merge `base_pdf_bytes` with attachment PDFs here.
                    # For now, we just let pdf_utils handle the main voucher PDF.

                    safe_vnum = "".join(
                        ch if ch.isalnum() or ch in ("-", "_") else "_"
                        for ch in str(display_vnum)
                    ) or "voucher"

                    act_right.download_button(
                        label="Download PDF",
                        data=base_pdf_bytes,
                        file_name=f"{safe_vnum}.pdf",
                        mime="application/pdf",
                        key=f"pdf_{voucher_id}",
                        help="Export voucher to PDF.",
                    )
                except Exception as e:
                    act_right.error(f"PDF error: {e}")
            else:
                act_right.info(
                    "Install reportlab to enable PDF download:  pip install reportlab"
                )

    st.markdown("</div>", unsafe_allow_html=True)





# ============================ TAB: INVOICES (ALL / EDIT) ====================
from invoices_module import (
    list_invoices,
    update_invoice,
    delete_invoice,
    compute_invoice_totals,
)
from crm_gateway import (
    get_vendor_name_list,
    get_payable_account_options,
    get_expense_asset_account_options,
)

with tabs[2]:
    st.markdown(
        "<div class='card'><div class='card-header'>📄 All Invoices</div>",
        unsafe_allow_html=True,
    )

    user = current_user()
    company_id = user["company_id"]
    username = user["username"]

    vendor_names = get_vendor_name_list(company_id)
    payable_accounts = get_payable_account_options(company_id)
    expense_accounts = get_expense_asset_account_options(company_id)

    invoices = list_invoices(company_id)

    if not invoices:
        st.info("No invoices yet.")
    else:
        for inv in invoices:
            inv_id = inv["id"]
            inv_no = inv.get("invoice_number") or f"INV-{inv_id}"
            vendor = inv.get("vendor") or ""
            currency = inv.get("currency") or "NGN"

            vatable_amount = float(inv.get("vatable_amount") or 0.0)
            non_vatable_amount = float(inv.get("non_vatable_amount") or 0.0)
            vat_rate = float(inv.get("vat_rate") or 0.0)
            wht_rate = float(inv.get("wht_rate") or 0.0)

            totals = compute_invoice_totals(
                vatable_amount=vatable_amount,
                non_vatable_amount=non_vatable_amount,
                vat_rate=vat_rate,
                wht_rate=wht_rate,
            )
            total_payable = totals["total"]

            with st.expander(
                f"INVOICE {inv_no} • {vendor} • TOTAL {money(total_payable, currency)}",
                expanded=False,
            ):
                c1, c2, c3 = st.columns([2, 2, 2])
                c1.text_input(
                    "Invoice Number",
                    value=inv_no,
                    key=f"invno_{inv_id}",
                    disabled=True,
                )

                new_vendor = c2.selectbox(
                    "Vendor",
                    vendor_names,
                    index=safe_index(vendor_names, vendor),
                    key=f"inv_vendor_{inv_id}",
                )

                new_currency = c3.text_input(
                    "Currency",
                    value=currency,
                    key=f"inv_currency_{inv_id}",
                )

                c4, c5 = st.columns([2, 4])
                new_vendor_inv = c4.text_input(
                    "Vendor Invoice Number",
                    value=inv.get("vendor_invoice_number") or "",
                    key=f"inv_vendorref_{inv_id}",
                )
                new_summary = c5.text_area(
                    "Summary / Description",
                    value=inv.get("summary") or "",
                    key=f"inv_summary_{inv_id}",
                )

                c6, c7, c8, c9 = st.columns([2, 2, 2, 2])
                new_vatable = c6.number_input(
                    "Vatable Amount",
                    value=vatable_amount,
                    min_value=0.0,
                    format="%.2f",
                    key=f"inv_vatable_{inv_id}",
                )
                new_non_vatable = c7.number_input(
                    "Non-Vatable Amount",
                    value=non_vatable_amount,
                    min_value=0.0,
                    format="%.2f",
                    key=f"inv_non_vatable_{inv_id}",
                )
                new_vat_rate = c8.number_input(
                    "VAT Rate (%)",
                    value=vat_rate,
                    min_value=0.0,
                    format="%.2f",
                    key=f"inv_vat_rate_{inv_id}",
                )
                new_wht_rate = c9.number_input(
                    "WHT Rate (%)",
                    value=wht_rate,
                    min_value=0.0,
                    format="%.2f",
                    key=f"inv_wht_rate_{inv_id}",
                )

                c10, c11 = st.columns([3, 3])
                new_payable_acct = c10.selectbox(
                    "Payable Account",
                    payable_accounts,
                    index=safe_index(
                        payable_accounts, inv.get("payable_account_name") or ""
                    ),
                    key=f"inv_pay_acct_{inv_id}",
                )
                new_expense_acct = c11.selectbox(
                    "Expense/Asset Account",
                    expense_accounts,
                    index=safe_index(
                        expense_accounts, inv.get("expense_account_name") or ""
                    ),
                    key=f"inv_exp_acct_{inv_id}",
                )

                # Recompute totals based on new inputs
                new_totals = compute_invoice_totals(
                    vatable_amount=new_vatable,
                    non_vatable_amount=new_non_vatable,
                    vat_rate=new_vat_rate,
                    wht_rate=new_wht_rate,
                )

                st.markdown(
                    f"**VAT:** {money(new_totals['vat'], new_currency)}  •  "
                    f"**WHT:** {money(new_totals['wht'], new_currency)}  •  "
                    f"**Subtotal:** {money(new_totals['subtotal'], new_currency)}  •  "
                    f"**TOTAL PAYABLE:** {money(new_totals['total'], new_currency)}"
                )

                file_col1, file_col2 = st.columns([3, 3])
                new_file = file_col1.file_uploader(
                    "Attach / Replace Invoice Document (PDF/JPG/PNG)",
                    type=["pdf", "jpg", "jpeg", "png"],
                    key=f"inv_file_{inv_id}",
                )

                act_l, act_r = st.columns([1, 1])

                if act_l.button("Save Changes", key=f"inv_save_{inv_id}"):
                    file_name = None
                    file_bytes = None
                    if new_file is not None:
                        file_name = new_file.name
                        file_bytes = new_file.read()

                    err = update_invoice(
                        company_id=company_id,
                        username=username,
                        invoice_id=inv_id,
                        vendor=new_vendor,
                        vendor_invoice_number=new_vendor_inv,
                        summary=new_summary,
                        vatable_amount=new_vatable,
                        non_vatable_amount=new_non_vatable,
                        vat_rate=new_vat_rate,
                        wht_rate=new_wht_rate,
                        payable_account_name=new_payable_acct,
                        expense_account_name=new_expense_acct,
                        currency=new_currency,
                        file_name=file_name,
                        file_bytes=file_bytes,
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success("Invoice updated.")
                        rerun()

                if act_r.button(
                    "Delete Invoice",
                    key=f"inv_delete_{inv_id}",
                ):
                    err = delete_invoice(company_id, inv_id, username)
                    if err:
                        st.error(err)
                    else:
                        st.success("Invoice deleted.")
                        rerun()

    st.markdown("</div>", unsafe_allow_html=True)






# ============================ TAB: VENDORS (LIST / EDIT) ====================
from crm_gateway import list_vendors, upsert_vendor, delete_vendor

with tabs[3]:
    st.markdown(
        "<div class='card'><div class='card-header'>🏢 All Vendors</div>",
        unsafe_allow_html=True,
    )

    user = current_user()
    company_id = user["company_id"]
    username = user["username"]

    vendors_list = list_vendors(company_id)

    if not vendors_list:
        st.info("No vendors yet. Create them in the CRM / Vendors form.")
    else:
        for ven in vendors_list:
            vid = ven["id"]
            name = ven.get("name") or ""
            bank_name = ven.get("bank_name") or ""
            bank_account = ven.get("bank_account") or ""

            header = f"{name}"
            if bank_name:
                header += f" • {bank_name}"
            if bank_account:
                header += f" • {bank_account}"

            with st.expander(f"VENDOR {header}", expanded=False):
                c1, c2 = st.columns([3, 3])
                new_name = c1.text_input(
                    "Vendor Name",
                    value=name,
                    key=f"ven_name_{vid}",
                )
                new_contact = c2.text_input(
                    "Contact Person",
                    value=ven.get("contact_person") or "",
                    key=f"ven_contact_{vid}",
                )

                c3, c4 = st.columns([3, 3])
                new_bank = c3.text_input(
                    "Bank Name",
                    value=bank_name,
                    key=f"ven_bank_{vid}",
                )
                new_bank_acct = c4.text_input(
                    "Bank Account",
                    value=bank_account,
                    key=f"ven_bankacct_{vid}",
                )

                new_notes = st.text_area(
                    "Notes",
                    value=ven.get("notes") or "",
                    key=f"ven_notes_{vid}",
                )

                act_l, act_r = st.columns([1, 1])

                if act_l.button("Save Vendor", key=f"ven_save_{vid}"):
                    err = upsert_vendor(
                        company_id=company_id,
                        name=new_name,
                        contact_person=new_contact,
                        bank_name=new_bank,
                        bank_account=new_bank_acct,
                        notes=new_notes,
                        username=username,
                        vendor_id=vid,
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success("Vendor updated.")
                        rerun()

                if act_r.button("Delete Vendor", key=f"ven_delete_{vid}"):
                    err = delete_vendor(company_id, vid)
                    if err:
                        st.error(err)
                    else:
                        st.success("Vendor deleted.")
                        rerun()

    st.markdown("</div>", unsafe_allow_html=True)




# ============================ TAB: STAFF (LIST / EDIT) ====================
from crm_gateway import list_staff, upsert_staff, delete_staff

with tabs[4]:
    st.markdown(
        "<div class='card'><div class='card-header'>👤 All Staff</div>",
        unsafe_allow_html=True,
    )

    user = current_user()
    company_id = user["company_id"]
    username = user["username"]

    staff_list = list_staff(company_id)

    if not staff_list:
        st.info("No staff yet. Create them in the CRM / Staff form.")
    else:
        for stf in staff_list:
            sid = stf["id"]
            full_name = f"{stf.get('first_name') or ''} {stf.get('last_name') or ''}".strip()
            status = stf.get("status") or ""
            position = stf.get("position") or ""

            header = f"{full_name}"
            if status:
                header += f" • {status}"
            if position:
                header += f" • {position}"

            with st.expander(f"STAFF {header}", expanded=False):
                c1, c2 = st.columns([3, 3])
                new_first = c1.text_input(
                    "First Name",
                    value=stf.get("first_name") or "",
                    key=f"stf_fn_{sid}",
                )
                new_last = c2.text_input(
                    "Last Name",
                    value=stf.get("last_name") or "",
                    key=f"stf_ln_{sid}",
                )

                c3, c4 = st.columns([3, 3])
                new_email = c3.text_input(
                    "Email",
                    value=stf.get("email") or "",
                    key=f"stf_email_{sid}",
                )
                new_phone = c4.text_input(
                    "Phone",
                    value=stf.get("phone") or "",
                    key=f"stf_phone_{sid}",
                )

                c5, c6 = st.columns([3, 3])
                new_status = c5.selectbox(
                    "Status",
                    ["active", "inactive", "on_leave", "other"],
                    index=safe_index(
                        ["active", "inactive", "on_leave", "other"],
                        status or "active",
                    ),
                    key=f"stf_status_{sid}",
                )
                new_position = c6.text_input(
                    "Position / Role",
                    value=position,
                    key=f"stf_position_{sid}",
                )

                act_l, act_r = st.columns([1, 1])

                if act_l.button("Save Staff", key=f"stf_save_{sid}"):
                    err = upsert_staff(
                        company_id=company_id,
                        first_name=new_first,
                        last_name=new_last,
                        email=new_email,
                        phone=new_phone,
                        status=new_status,
                        position=new_position,
                        staff_id=sid,
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success("Staff updated.")
                        rerun()

                if act_r.button("Delete Staff", key=f"stf_delete_{sid}"):
                    err = delete_staff(company_id, sid)
                    if err:
                        st.error(err)
                    else:
                        st.success("Staff deleted.")
                        rerun()

    st.markdown("</div>", unsafe_allow_html=True)









# -------------------
# Reports
# -------------------

def app_reports():
    # Any logged-in user can see reports
    require_login()
    user = current_user()
    company_id = user["company_id"]

    # Permissions for actions from reports
    can_modify = bool(user.get("can_create_voucher") or user.get("can_approve_voucher"))
    can_approve = bool(user.get("can_approve_voucher"))

    st.subheader("Reports")

    tab1, tab2, tab3 = st.tabs(["Voucher Register", "Invoice Register", "CRM / Master Data"])

    # Voucher report
    with tab1:
        st.markdown("### Voucher Register")
        vdf = pd.DataFrame(list_vouchers(company_id=company_id))
        if vdf.empty:
            st.info("No vouchers yet.")
        else:
            # Simple filters: by vendor and status if those columns exist
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

            st.markdown("### Voucher Actions (per voucher)")
            for _, row in vdf.iterrows():
                vid = int(row["id"])
                header_text = f"#{vid} – {row.get('voucher_number', '')} – {row.get('vendor', '')} (Status: {row.get('status', '')})"
                with st.expander(header_text, expanded=False):
                    st.write("**Basic Info**")
                    st.write(f"Voucher ID: {vid}")
                    st.write(f"Voucher Number: {row.get('voucher_number', '')}")
                    st.write(f"Vendor: {row.get('vendor', '')}")
                    st.write(f"Requester: {row.get('requester', '')}")
                    st.write(f"Invoice Ref: {row.get('invoice_ref', '')}")
                    st.write(f"Currency: {row.get('currency', '')}")
                    st.write(f"Status: {row.get('status', '')}")
                    st.write(f"Created At: {row.get('created_at', '')}")
                    st.write(f"Last Modified: {row.get('last_modified', '')}")
                    st.write(f"Approved By: {row.get('approved_by', '')}")
                    st.write(f"Approved At: {row.get('approved_at', '')}")

                    if not can_modify:
                        st.info("You have view-only access. Contact an admin to update or delete vouchers.")
                    else:
                        st.markdown("---")
                        c1, c2, c3 = st.columns(3)
                        current_status = (row.get("status") or "draft").lower()
                        status_options = ["draft", "submitted", "approved", "rejected"]
                        try:
                            default_status_index = status_options.index(current_status)
                        except ValueError:
                            default_status_index = 0

                        with c1:
                            new_status = st.selectbox(
                                "Change Status",
                                status_options,
                                index=default_status_index,
                                key=f"v_status_{vid}",
                            )

                        with c2:
                            if st.button(
                                "Update Status",
                                key=f"v_update_{vid}",
                            ):
                                if new_status in ("approved", "rejected") and not can_approve:
                                    st.error("You do not have permission to approve or reject vouchers.")
                                else:
                                    err = change_voucher_status(
                                        company_id=company_id,
                                        voucher_id=vid,
                                        new_status=new_status,
                                        actor_username=user["username"],
                                    )
                                    if err:
                                        st.error(err)
                                    else:
                                        st.success(f"Voucher {vid} updated to status '{new_status}'.")
                                        st.experimental_rerun()

                        with c3:
                            if st.button(
                                "Delete Voucher",
                                key=f"v_delete_{vid}",
                            ):
                                # Optional: restrict deletes to approvers
                                if not can_approve:
                                    st.error("You do not have permission to delete vouchers.")
                                else:
                                    err = delete_voucher(
                                        company_id=company_id,
                                        voucher_id=vid,
                                        actor_username=user["username"],
                                    )
                                    if err:
                                        st.error(err)
                                    else:
                                        st.success(f"Voucher {vid} deleted.")
                                        st.experimental_rerun()

    # Invoice report
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
                vendor_filter = st.selectbox("Filter by Vendor", vendors, key="inv_vendor_filter")
                if vendor_filter != "(All)":
                    idf = idf[idf["vendor"] == vendor_filter]

            if "currency" in idf.columns:
                currencies = ["(All)"] + sorted(
                    [c for c in idf["currency"].dropna().unique().tolist()]
                )
                currency_filter = st.selectbox("Filter by Currency", currencies, key="inv_currency_filter")
                if currency_filter != "(All)":
                    idf = idf[idf["currency"] == currency_filter]

            st.dataframe(idf)

            st.markdown("### Invoice Actions (per invoice)")

            # CRM options for editing
            vendor_options = get_vendor_name_list(company_id)
            payable_options = get_payable_account_options(company_id)
            expense_asset_options = get_expense_asset_account_options(company_id)

            for _, row in idf.iterrows():
                iid = int(row["id"])
                header_text = f"#{iid} – {row.get('invoice_number', '')} – {row.get('vendor', '')} ({row.get('currency', '')})"
                with st.expander(header_text, expanded=False):
                    st.write("**Current Values**")
                    st.write(f"Invoice ID: {iid}")
                    st.write(f"Invoice Number: {row.get('invoice_number', '')}")
                    st.write(f"Vendor: {row.get('vendor', '')}")
                    st.write(f"Vendor Invoice No.: {row.get('vendor_invoice_number', '')}")
                    st.write(f"Summary: {row.get('summary', '')}")
                    st.write(f"Currency: {row.get('currency', '')}")
                    st.write(f"Vatable Amount: {row.get('vatable_amount', '')}")
                    st.write(f"Non-vatable Amount: {row.get('non_vatable_amount', '')}")
                    st.write(f"VAT Rate: {row.get('vat_rate', '')}")
                    st.write(f"WHT Rate: {row.get('wht_rate', '')}")
                    st.write(f"Payable Account: {row.get('payable_account', '')}")
                    st.write(f"Expense / Asset Account: {row.get('expense_asset_account', '')}")
                    st.write(f"Terms: {row.get('terms', '')}")
                    st.write(f"Last Modified: {row.get('last_modified', '')}")

                    if not can_modify:
                        st.info("You have view-only access. Contact an admin to update or delete invoices.")
                    else:
                        st.markdown("---")
                        st.write("**Edit Invoice**")

                        with st.form(f"edit_invoice_form_{iid}"):
                            # Vendor
                            vendor_value = row.get("vendor") or ""
                            if vendor_value in vendor_options:
                                vendor_index = vendor_options.index(vendor_value)
                            else:
                                vendor_index = 0 if vendor_options else 0

                            vendor = st.selectbox(
                                "Vendor (from CRM)",
                                vendor_options,
                                index=vendor_index if vendor_options else 0,
                                key=f"inv_edit_vendor_{iid}",
                            )

                            vendor_invoice_number = st.text_input(
                                "Vendor Invoice Number",
                                value=row.get("vendor_invoice_number") or "",
                                key=f"inv_edit_vendor_inv_no_{iid}",
                            )

                            summary = st.text_area(
                                "Summary",
                                value=row.get("summary") or "",
                                key=f"inv_edit_summary_{iid}",
                            )

                            vatable_amount = st.number_input(
                                "Vatable Amount",
                                min_value=0.0,
                                step=0.01,
                                value=float(row.get("vatable_amount") or 0.0),
                                key=f"inv_edit_vatable_{iid}",
                            )

                            vat_rate = st.number_input(
                                "VAT Rate (%)",
                                min_value=0.0,
                                step=0.5,
                                value=float(row.get("vat_rate") or 0.0),
                                key=f"inv_edit_vat_rate_{iid}",
                            )

                            wht_rate = st.number_input(
                                "WHT Rate (%)",
                                min_value=0.0,
                                step=0.5,
                                value=float(row.get("wht_rate") or 0.0),
                                key=f"inv_edit_wht_rate_{iid}",
                            )

                            non_vatable_amount = st.number_input(
                                "Non-vatable Amount",
                                min_value=0.0,
                                step=0.01,
                                value=float(row.get("non_vatable_amount") or 0.0),
                                key=f"inv_edit_non_vatable_{iid}",
                            )

                            terms = st.text_area(
                                "Terms",
                                value=row.get("terms") or "",
                                key=f"inv_edit_terms_{iid}",
                            )

                            currency_value = (row.get("currency") or "NGN").upper()
                            currency_options = ["NGN", "USD", "GBP", "EUR"]
                            if currency_value not in currency_options:
                                currency_options.append(currency_value)
                            try:
                                currency_index = currency_options.index(currency_value)
                            except ValueError:
                                currency_index = 0

                            currency = st.selectbox(
                                "Currency",
                                currency_options,
                                index=currency_index,
                                key=f"inv_edit_currency_{iid}",
                            )

                            # Accounts
                            payable_value = row.get("payable_account") or ""
                            if payable_value in payable_options:
                                payable_index = payable_options.index(payable_value)
                            else:
                                payable_index = 0 if payable_options else 0

                            payable_account = st.selectbox(
                                "Payable Account (Chart of Accounts)",
                                payable_options,
                                index=payable_index if payable_options else 0,
                                key=f"inv_edit_payable_{iid}",
                            )

                            expense_value = row.get("expense_asset_account") or ""
                            if expense_value in expense_asset_options:
                                expense_index = expense_asset_options.index(expense_value)
                            else:
                                expense_index = 0 if expense_asset_options else 0

                            expense_asset_account = st.selectbox(
                                "Expense / Asset Account (Chart of Accounts)",
                                expense_asset_options,
                                index=expense_index if expense_asset_options else 0,
                                key=f"inv_edit_expense_{iid}",
                            )

                            uploaded_file = st.file_uploader(
                                "Replace invoice document (optional)",
                                type=["pdf", "jpg", "png"],
                                key=f"inv_edit_file_{iid}",
                            )
                            file_name = None
                            file_bytes = None
                            if uploaded_file is not None:
                                file_name = uploaded_file.name
                                file_bytes = uploaded_file.read()

                            save_btn = st.form_submit_button("Save Changes")
                            if save_btn:
                                err = update_invoice(
                                    company_id=company_id,
                                    invoice_id=iid,
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
                                    username=user["username"],
                                    file_name=file_name,
                                    file_data=file_bytes,
                                )
                                if err:
                                    st.error(err)
                                else:
                                    st.success(f"Invoice {iid} updated successfully.")
                                    st.experimental_rerun()

                        st.markdown("---")
                        if st.button(
                            "Delete Invoice",
                            key=f"inv_delete_{iid}",
                        ):
                            # Let same permission set handle delete
                            if not can_modify:
                                st.error("You do not have permission to delete invoices.")
                            else:
                                err = delete_invoice(
                                    company_id=company_id,
                                    invoice_id=iid,
                                    username=user["username"],
                                )
                                if err:
                                    st.error(err)
                                else:
                                    st.success(f"Invoice {iid} deleted.")
                                    st.experimental_rerun()

    # CRM / master data overview
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
