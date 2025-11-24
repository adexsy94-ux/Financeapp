# app_main.py
# Streamlit entrypoint for finance app: vouchers + invoices (with inline edit/delete in reporting).

import streamlit as st
import pandas as pd

from db_config import connect
from crm_gateway import (
    get_vendor_name_list,
    get_requester_options,
    get_payable_account_options,
    get_expense_asset_account_options,
)
from vouchers_module import (
    init_voucher_schema,
    list_vouchers,
    create_voucher,
    update_voucher,
    change_voucher_status,
    delete_voucher,
    get_voucher_with_lines,
)
from invoices_module import (
    create_invoice,
    list_invoices,
    update_invoice,
    delete_invoice,
)
from pdf_utils import build_voucher_pdf_bytes


# -------------------------------------------------------------------
# SIMPLE AUTH STUBS (because there is no auth.py in your project)
# -------------------------------------------------------------------

def current_user():
    """
    Very simple placeholder user.
    Replace this with real authentication later if needed.
    """
    return {
        "username": "admin",
        "company_id": 1,
    }


def require_permission(permission_name: str):
    """
    Placeholder permission check that does nothing for now.
    If you later implement auth/roles, add checks here.
    """
    return


# -------------------
# Vouchers
# -------------------

def app_vouchers():
    require_permission("can_create_voucher")
    user = current_user()
    username = user["username"]
    company_id = user["company_id"]

    # track which voucher is in edit mode (full form below)
    if "editing_voucher_id" not in st.session_state:
        st.session_state["editing_voucher_id"] = None

    st.header("Vouchers")

    # ---------------------------
    # CREATE NEW VOUCHER SECTION
    # ---------------------------
    vendor_options = get_vendor_name_list(company_id)
    requester_options = get_requester_options(company_id)
    account_options = get_expense_asset_account_options(company_id)

    st.subheader("Create Voucher")

    vendor = st.selectbox("Vendor (from CRM)", vendor_options, key="v_new_vendor")
    requester = st.selectbox(
        "Requester (Staff in CRM)", requester_options, key="v_new_requester"
    )

    # Link vouchers to invoices for this vendor
    all_invoices = list_invoices(company_id=company_id)
    invoice_numbers_for_vendor = [
        row["invoice_number"] for row in all_invoices if row.get("vendor") == vendor
    ]
    invoice_choices = ["(None)"] + invoice_numbers_for_vendor
    invoice_choice = st.selectbox(
        "Invoice / Reference (all invoices for selected vendor)",
        invoice_choices,
        key="v_new_invoice_ref",
    )
    invoice_ref = "" if invoice_choice == "(None)" else invoice_choice

    # Find the selected invoice row (if any)
    selected_invoice = next(
        (inv for inv in all_invoices if inv.get("invoice_number") == invoice_choice),
        None,
    )

    # ---- Invoice allocation summary ----
    invoice_currency = None
    actual_balance = vat_balance = wht_balance = None
    if selected_invoice is not None:
        invoice_currency = (selected_invoice.get("currency") or "NGN").upper()
        inv_vatable = float(selected_invoice.get("vatable_amount") or 0.0)
        inv_non_vatable = float(selected_invoice.get("non_vatable_amount") or 0.0)
        inv_vat_total = float(selected_invoice.get("vat_amount") or 0.0)
        inv_wht_total = float(selected_invoice.get("wht_amount") or 0.0)
        actual_total = inv_vatable + inv_non_vatable

        amount_paid = vat_paid = wht_paid = 0.0
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
                        (company_id, invoice_choice, invoice_currency),
                    )
                    row = cur.fetchone()
                    if row:
                        amount_paid = float(row[0] or 0.0)
                        vat_paid = float(row[1] or 0.0)
                        wht_paid = float(row[2] or 0.0)
        except Exception:
            amount_paid = vat_paid = wht_paid = 0.0

        actual_balance = actual_total - amount_paid
        vat_balance = inv_vat_total - vat_paid
        wht_balance = inv_wht_total - wht_paid

        st.markdown("### Invoice Allocation Summary")
        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown("**Base Amount (Invoice)**")
            st.write(f"Invoice base total: **{actual_total:,.2f} {invoice_currency}**")
            st.markdown(
                "Base paid via vouchers: "
                f"<span style='color: green; font-weight:bold;'>{amount_paid:,.2f} {invoice_currency}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "Base Balance to Pay: "
                f"<span style='color: red; font-weight:bold;'>{actual_balance:,.2f} {invoice_currency}</span>",
                unsafe_allow_html=True,
            )

        with c2:
            st.markdown("**VAT Allocation**")
            st.write(f"Invoice VAT: **{inv_vat_total:,.2f} {invoice_currency}**")
            st.markdown(
                "VAT paid via vouchers: "
                f"<span style='color: green; font-weight:bold;'>{vat_paid:,.2f} {invoice_currency}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "VAT Balance: "
                f"<span style='color: red; font-weight:bold;'>{vat_balance:,.2f} {invoice_currency}</span>",
                unsafe_allow_html=True,
            )

        with c3:
            st.markdown("**WHT Allocation**")
            st.write(f"Invoice WHT: **{inv_wht_total:,.2f} {invoice_currency}**")
            st.markdown(
                "WHT Deducted via Vouchers: "
                f"<span style='color: green; font-weight:bold;'>{wht_paid:,.2f} {invoice_currency}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "WHT Balance: "
                f"<span style='color: red; font-weight:bold;'>{wht_balance:,.2f} {invoice_currency}</span>",
                unsafe_allow_html=True,
            )

        st.markdown("---")

    # Currency selection (default from invoice)
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
        key="v_new_currency",
    )

    uploaded = st.file_uploader(
        "Attach supporting document (optional)",
        type=["pdf", "jpg", "png"],
        key="v_new_file",
    )
    file_name = file_bytes = None
    if uploaded is not None:
        file_name = uploaded.name
        file_bytes = uploaded.read()

    st.markdown("**Voucher Lines**")
    lines = []
    num_lines = st.number_input(
        "Number of lines",
        min_value=1,
        max_value=20,
        value=1,
        step=1,
        key="v_new_num_lines",
    )
    for i in range(int(num_lines)):
        st.markdown(f"**Line {i+1}**")
        col1, col2, col3, col4, col5 = st.columns([3, 1, 2, 1, 1])
        with col1:
            desc = st.text_input("Description", key=f"v_new_desc_{i}")
        with col2:
            amt = st.number_input(
                "Amount", key=f"v_new_amt_{i}", min_value=0.0, step=0.01
            )
        with col3:
            acct = st.selectbox(
                "Expense / Asset Account (Chart of Accounts)",
                account_options,
                key=f"v_new_acct_{i}",
            )
        with col4:
            vat = st.number_input(
                "VAT %", key=f"v_new_vat_{i}", min_value=0.0, step=0.5
            )
        with col5:
            wht = st.number_input(
                "WHT %", key=f"v_new_wht_{i}", min_value=0.0, step=0.5
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

    total_amount = sum((l.get("amount") or 0.0) for l in lines)
    total_vat = sum(
        round((l.get("amount") or 0.0) * (l.get("vat_percent") or 0.0) / 100.0, 2)
        for l in lines
    )
    total_wht = sum(
        round((l.get("amount") or 0.0) * (l.get("wht_percent") or 0.0) / 100.0, 2)
        for l in lines
    )

    validation_errors = []
    if selected_invoice is not None:
        cur_code = invoice_currency or "NGN"

        if actual_balance is not None and total_amount > actual_balance + 0.0001:
            validation_errors.append(
                f"Total Amount in voucher lines ({total_amount:,.2f} {cur_code}) "
                f"is greater than the Base Balance to Pay ({actual_balance:,.2f} {cur_code})."
            )

        if vat_balance is not None and total_vat > vat_balance + 0.0001:
            validation_errors.append(
                f"Total VAT amount in voucher lines ({total_vat:,.2f} {cur_code}) "
                f"is greater than the VAT Balance ({vat_balance:,.2f} {cur_code})."
            )

        if wht_balance is not None and total_wht > wht_balance + 0.0001:
            validation_errors.append(
                f"Total WHT amount in voucher lines ({total_wht:,.2f} {cur_code}) "
                f"is greater than the WHT Balance ({wht_balance:,.2f} {cur_code})."
            )

    if validation_errors:
        for msg in validation_errors:
            st.error(msg)

    if st.button("Save Voucher", key="v_new_save"):
        if validation_errors:
            st.error(
                "Voucher not saved because one or more line totals are higher than the "
                "remaining invoice balances shown above. Please adjust the amounts."
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

    # ---------------------------
    # REPORTING SECTION
    # ---------------------------
    st.markdown("---")
    st.subheader("Voucher Report")

    vouchers = list_vouchers(company_id=company_id)
    if not vouchers:
        st.info("No vouchers yet.")
        return

    # Each voucher row → expander with status / edit / delete
    for v in vouchers:
        title = (
            f"{v['voucher_number']} | Vendor: {v['vendor']} | "
            f"Status: {v['status']} | ID: {v['id']}"
        )
        with st.expander(title, expanded=False):
            st.write(f"**Voucher ID:** {v['id']}")
            st.write(f"**Vendor:** {v['vendor']}")
            st.write(f"**Requester:** {v['requester']}")
            st.write(f"**Invoice Ref:** {v.get('invoice_ref') or ''}")
            st.write(f"**Currency:** {v.get('currency') or ''}")
            st.write(f"**Created:** {v.get('created_at')}")
            st.write(f"**Last Modified:** {v.get('last_modified')}")
            st.write(f"**Status:** {v.get('status')}")

            # show lines (read-only) for quick view
            header_data, v_lines = get_voucher_with_lines(company_id, v["id"])
            if v_lines:
                st.markdown("**Lines**")
                st.dataframe(pd.DataFrame(v_lines))

            st.markdown("---")
            st.markdown("### Update status / Edit / Delete")

            # Status update for this voucher only (per row)
            col_s1, col_s2 = st.columns([2, 1])
            with col_s1:
                action = st.selectbox(
                    "Update status",
                    ["--", "draft", "submitted", "approved", "rejected"],
                    index=0,
                    key=f"v_status_action_{v['id']}",
                )
            with col_s2:
                if st.button("Apply status", key=f"v_status_apply_{v['id']}"):
                    if action == "--":
                        st.error("Please select a status.")
                    else:
                        if action in ("approved", "rejected"):
                            require_permission("can_approve_voucher")

                        err = change_voucher_status(
                            company_id=company_id,
                            voucher_id=v["id"],
                            new_status=action,
                            actor_username=username,
                        )
                        if err:
                            st.error(err)
                        else:
                            st.success(
                                f"Voucher {v['id']} status updated to '{action}'."
                            )
                            st.experimental_rerun()

            st.markdown("---")
            col_e1, col_e2 = st.columns(2)

            # Edit – sets editing_voucher_id and shows full form below
            with col_e1:
                if st.button("Edit this voucher", key=f"v_edit_btn_{v['id']}"):
                    st.session_state["editing_voucher_id"] = v["id"]
                    st.experimental_rerun()

            # Delete – per row with confirm
            with col_e2:
                confirm_delete = st.checkbox(
                    "Confirm delete", key=f"v_del_confirm_{v['id']}"
                )
                if st.button("Delete this voucher", key=f"v_del_btn_{v['id']}"):
                    if not confirm_delete:
                        st.error("Tick 'Confirm delete' first.")
                    else:
                        err = delete_voucher(
                            company_id=company_id,
                            voucher_id=v["id"],
                            actor_username=username,
                        )
                        if err:
                            st.error(err)
                        else:
                            st.success(f"Voucher {v['id']} deleted.")
                            st.experimental_rerun()

            # PDF export for this voucher
            st.markdown("---")
            if st.button("Download PDF for this voucher", key=f"v_pdf_{v['id']}"):
                try:
                    pdf_bytes = build_voucher_pdf_bytes(
                        company_id=company_id, voucher_id=v["id"]
                    )
                except Exception as e:
                    st.error(f"Error generating PDF: {e}")
                else:
                    st.download_button(
                        label="Download PDF",
                        data=pdf_bytes,
                        file_name=f"voucher_{v['id']}.pdf",
                        mime="application/pdf",
                        key=f"v_pdf_dl_{v['id']}",
                    )

    # ---------------------------
    # FULL EDIT FORM (below report)
    # ---------------------------
    editing_id = st.session_state.get("editing_voucher_id")
    if editing_id:
        st.markdown("---")
        st.subheader(f"Edit Voucher ID {editing_id}")
        header, v_lines = get_voucher_with_lines(company_id, editing_id)

        edit_vendor = st.text_input(
            "Vendor", value=header.get("vendor") or "", key="v_edit_vendor"
        )
        edit_requester = st.text_input(
            "Requester", value=header.get("requester") or "", key="v_edit_requester"
        )
        edit_invoice_ref = st.text_input(
            "Invoice Ref",
            value=header.get("invoice_ref") or "",
            key="v_edit_invoice_ref",
        )
        edit_currency = st.text_input(
            "Currency",
            value=header.get("currency") or "NGN",
            key="v_edit_currency",
        )

        edit_lines = []
        st.markdown("**Edit Voucher Lines**")
        max_rows = max(5, len(v_lines))
        for i in range(max_rows):
            if i < len(v_lines):
                ln = v_lines[i]
                desc_default = ln["description"]
                account_default = ln["account_name"]
                amount_default = float(ln["amount"] or 0.0)
                vat_default = float(ln["vat_percent"] or 0.0)
                wht_default = float(ln["wht_percent"] or 0.0)
            else:
                desc_default = ""
                account_default = ""
                amount_default = 0.0
                vat_default = 0.0
                wht_default = 0.0

            st.markdown(f"**Line {i+1}**")
            c1, c2, c3, c4, c5 = st.columns([3, 3, 2, 2, 2])
            with c1:
                desc = st.text_input(
                    "Description",
                    value=desc_default,
                    key=f"v_edit_desc_{editing_id}_{i}",
                )
            with c2:
                acct = st.text_input(
                    "Account",
                    value=account_default,
                    key=f"v_edit_acct_{editing_id}_{i}",
                )
            with c3:
                amt = st.number_input(
                    "Amount",
                    value=amount_default,
                    min_value=0.0,
                    step=0.01,
                    key=f"v_edit_amt_{editing_id}_{i}",
                )
            with c4:
                vat = st.number_input(
                    "VAT %",
                    value=vat_default,
                    min_value=0.0,
                    step=0.5,
                    key=f"v_edit_vat_{editing_id}_{i}",
                )
            with c5:
                wht = st.number_input(
                    "WHT %",
                    value=wht_default,
                    min_value=0.0,
                    step=0.5,
                    key=f"v_edit_wht_{editing_id}_{i}",
                )

            edit_lines.append(
                {
                    "description": desc,
                    "account_name": acct,
                    "amount": amt,
                    "vat_percent": vat,
                    "wht_percent": wht,
                }
            )

        col_f1, col_f2 = st.columns(2)
        with col_f1:
            if st.button("Save changes", key="v_edit_save"):
                err = update_voucher(
                    company_id=company_id,
                    voucher_id=editing_id,
                    username=username,
                    vendor=edit_vendor,
                    requester=edit_requester,
                    invoice_ref=edit_invoice_ref,
                    currency=edit_currency,
                    lines=edit_lines,
                )
                if err:
                    st.error(err)
                else:
                    st.success("Voucher updated.")
                    st.session_state["editing_voucher_id"] = None
                    st.experimental_rerun()
        with col_f2:
            if st.button("Cancel edit", key="v_edit_cancel"):
                st.session_state["editing_voucher_id"] = None
                st.experimental_rerun()


# -------------------
# Invoices
# -------------------

def app_invoices():
    require_permission("can_create_voucher")
    user = current_user()
    username = user["username"]
    company_id = user["company_id"]

    st.header("Invoices")

    vendor_options = get_vendor_name_list(company_id)
    payable_options = get_payable_account_options(company_id)
    expense_asset_options = get_expense_asset_account_options(company_id)

    # ---------------------------
    # CREATE NEW INVOICE SECTION
    # ---------------------------
    st.subheader("Create Invoice")
    st.info("Invoice number will be auto-generated when you save.")

    vendor = st.selectbox("Vendor (from CRM)", vendor_options, key="inv_new_vendor")
    vendor_invoice_number = st.text_input(
        "Vendor Invoice Number", key="inv_new_vendor_inv_no"
    )
    summary = st.text_area("Summary", key="inv_new_summary")

    vatable_amount = st.number_input(
        "Vatable Amount", min_value=0.0, step=0.01, key="inv_new_vatable"
    )
    vat_rate = st.number_input(
        "VAT Rate (%)", min_value=0.0, step=0.5, key="inv_new_vat_rate"
    )
    wht_rate = st.number_input(
        "WHT Rate (%)", min_value=0.0, step=0.5, key="inv_new_wht_rate"
    )
    non_vatable_amount = st.number_input(
        "Non-vatable Amount", min_value=0.0, step=0.01, key="inv_new_non_vatable"
    )

    terms = st.text_area("Terms", key="inv_new_terms")

    currency = st.selectbox(
        "Currency",
        ["NGN", "USD", "GBP", "EUR"],
        index=0,
        key="inv_new_currency",
    )

    payable_account = st.selectbox(
        "Payable Account (Chart of Accounts)",
        payable_options,
        key="inv_new_payable",
    )
    expense_asset_account = st.selectbox(
        "Expense / Asset Account (Chart of Accounts)",
        expense_asset_options,
        key="inv_new_expense",
    )

    uploaded = st.file_uploader(
        "Attach invoice document (optional)",
        type=["pdf", "jpg", "png"],
        key="inv_new_file",
    )
    file_name = file_bytes = None
    if uploaded is not None:
        file_name = uploaded.name
        file_bytes = uploaded.read()

    if st.button("Save Invoice", key="inv_new_save"):
        err = None
        try:
            create_invoice(
                company_id=company_id,
                username=username,
                invoice_number="",  # backend auto-generates
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
            st.experimental_rerun()

    # ---------------------------
    # REPORTING SECTION
    # ---------------------------
    st.markdown("---")
    st.subheader("Invoice Report")

    invoices = list_invoices(company_id=company_id)
    if not invoices:
        st.info("No invoices yet.")
        return

    idf = pd.DataFrame(invoices)

    # each invoice row -> expander with inline edit + delete
    for _, row in idf.iterrows():
        inv_id = int(row["id"])
        title = f"{row['invoice_number']} | Vendor: {row.get('vendor','')} | ID: {inv_id}"
        with st.expander(title, expanded=False):
            st.write(f"**Invoice ID:** {inv_id}")
            st.write(f"**Vendor:** {row.get('vendor','')}")
            st.write(f"**Vendor Invoice No.:** {row.get('vendor_invoice_number','')}")
            st.write(f"**Summary:** {row.get('summary','')}")
            st.write(f"**Currency:** {row.get('currency','')}")
            st.write(f"**Vatable:** {row.get('vatable_amount')}")
            st.write(f"**Non-vatable:** {row.get('non_vatable_amount')}")
            st.write(f"**VAT Rate:** {row.get('vat_rate')}")
            st.write(f"**WHT Rate:** {row.get('wht_rate')}")
            st.write(f"**Terms:** {row.get('terms','')}")
            st.write(f"**Created:** {row.get('created_at')}")
            st.write(f"**Last Modified:** {row.get('last_modified')}")

            st.markdown("---")
            st.markdown("### Edit / Delete this invoice")

            with st.form(f"inv_edit_form_{inv_id}"):
                # EDIT FIELDS
                current_vendor = row.get("vendor") or ""
                if current_vendor in vendor_options:
                    vendor_idx = vendor_options.index(current_vendor)
                else:
                    vendor_idx = 0

                edit_vendor = st.selectbox(
                    "Vendor (from CRM)",
                    vendor_options,
                    index=vendor_idx,
                    key=f"inv_edit_vendor_{inv_id}",
                )
                edit_vendor_invoice_number = st.text_input(
                    "Vendor Invoice Number",
                    value=row.get("vendor_invoice_number") or "",
                    key=f"inv_edit_vendor_inv_no_{inv_id}",
                )
                edit_summary = st.text_area(
                    "Summary",
                    value=row.get("summary") or "",
                    key=f"inv_edit_summary_{inv_id}",
                )

                edit_vatable_amount = st.number_input(
                    "Vatable Amount",
                    min_value=0.0,
                    step=0.01,
                    value=float(row.get("vatable_amount") or 0.0),
                    key=f"inv_edit_vatable_{inv_id}",
                )
                edit_vat_rate = st.number_input(
                    "VAT Rate (%)",
                    min_value=0.0,
                    step=0.5,
                    value=float(row.get("vat_rate") or 0.0),
                    key=f"inv_edit_vat_rate_{inv_id}",
                )
                edit_wht_rate = st.number_input(
                    "WHT Rate (%)",
                    min_value=0.0,
                    step=0.5,
                    value=float(row.get("wht_rate") or 0.0),
                    key=f"inv_edit_wht_rate_{inv_id}",
                )
                edit_non_vatable_amount = st.number_input(
                    "Non-vatable Amount",
                    min_value=0.0,
                    step=0.01,
                    value=float(row.get("non_vatable_amount") or 0.0),
                    key=f"inv_edit_non_vatable_{inv_id}",
                )

                edit_terms = st.text_area(
                    "Terms",
                    value=row.get("terms") or "",
                    key=f"inv_edit_terms_{inv_id}",
                )

                current_currency = (row.get("currency") or "NGN").upper()
                currency_options = ["NGN", "USD", "GBP", "EUR"]
                if current_currency not in currency_options:
                    currency_options.append(current_currency)
                try:
                    curr_idx = currency_options.index(current_currency)
                except ValueError:
                    curr_idx = 0

                edit_currency = st.selectbox(
                    "Currency",
                    currency_options,
                    index=curr_idx,
                    key=f"inv_edit_currency_{inv_id}",
                )

                current_payable = row.get("payable_account") or ""
                if current_payable in payable_options:
                    payable_idx = payable_options.index(current_payable)
                else:
                    payable_idx = 0

                edit_payable_account = st.selectbox(
                    "Payable Account (Chart of Accounts)",
                    payable_options,
                    index=payable_idx,
                    key=f"inv_edit_payable_{inv_id}",
                )

                current_expense = row.get("expense_asset_account") or ""
                if current_expense in expense_asset_options:
                    expense_idx = expense_asset_options.index(current_expense)
                else:
                    expense_idx = 0

                edit_expense_asset_account = st.selectbox(
                    "Expense / Asset Account (Chart of Accounts)",
                    expense_asset_options,
                    index=expense_idx,
                    key=f"inv_edit_expense_{inv_id}",
                )

                st.markdown("**Replace invoice document (optional)**")
                edit_uploaded = st.file_uploader(
                    "Upload new invoice document (leave empty to keep existing)",
                    type=["pdf", "jpg", "png"],
                    key=f"inv_edit_file_{inv_id}",
                )
                edit_file_name = edit_file_bytes = None
                if edit_uploaded is not None:
                    edit_file_name = edit_uploaded.name
                    edit_file_bytes = edit_uploaded.read()

                confirm_delete = st.checkbox(
                    "Confirm delete this invoice",
                    key=f"inv_confirm_del_{inv_id}",
                )

                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    submitted_update = st.form_submit_button("Update Invoice")
                with col_b2:
                    submitted_delete = st.form_submit_button("Delete Invoice")

            # handle form actions
            if submitted_update:
                err = update_invoice(
                    company_id=company_id,
                    invoice_id=inv_id,
                    vendor_invoice_number=edit_vendor_invoice_number,
                    vendor=edit_vendor,
                    summary=edit_summary,
                    vatable_amount=edit_vatable_amount,
                    vat_rate=edit_vat_rate,
                    wht_rate=edit_wht_rate,
                    non_vatable_amount=edit_non_vatable_amount,
                    terms=edit_terms,
                    payable_account=edit_payable_account,
                    expense_asset_account=edit_expense_asset_account,
                    currency=edit_currency,
                    username=username,
                    file_name=edit_file_name,
                    file_data=edit_file_bytes,
                )
                if err:
                    st.error(err)
                else:
                    st.success(f"Invoice {row['invoice_number']} updated.")
                    st.experimental_rerun()

            if submitted_delete:
                if not confirm_delete:
                    st.error("Tick the confirmation box to delete this invoice.")
                else:
                    err = delete_invoice(
                        company_id=company_id,
                        invoice_id=inv_id,
                        username=username,
                    )
                    if err:
                        st.error(err)
                    else:
                        st.success(f"Invoice {row['invoice_number']} deleted.")
                        st.experimental_rerun()


# -------------------
# Main
# -------------------

def main():
    st.set_page_config(page_title="Finance App", layout="wide")

    # ensure voucher tables exist
    try:
        init_voucher_schema()
    except Exception:
        # if schema init fails, still let the app try to run
        pass

    st.sidebar.title("Navigation")
    page = st.sidebar.radio(
        "Go to",
        ["Vouchers", "Invoices"],
        index=0,
    )

    if page == "Vouchers":
        app_vouchers()
    elif page == "Invoices":
        app_invoices()


if __name__ == "__main__":
    main()
