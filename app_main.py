from io import BytesIO
from contextlib import closing
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from auth_module import current_user, require_login, require_permission
from crm_gateway import (
    get_vendor_name_list,
    get_requester_options,
    get_expense_asset_account_options,
)
from db_config import connect
from invoices_module import list_invoices
from vouchers_module import (
    init_voucher_schema,
    list_vouchers,
    list_voucher_lines,
    update_voucher,
    create_voucher,
    change_voucher_status,
    delete_voucher,
    generate_voucher_number,
)


# =====================================================================
# TAB: VOUCHERS – UPDATED app_vouchers() with Voucher Number field
# =====================================================================

def app_vouchers():
    require_permission("can_create_voucher")
    user = current_user()
    if not user:
        require_login()
        return

    username = user["username"]
    company_id = user["company_id"]

    # Ensure schema exists (safe to call multiple times)
    init_voucher_schema()

    st.markdown(
        "<div class='card'><div class='card-header'>🧾 Create Voucher</div>",
        unsafe_allow_html=True,
    )

    # --- CRM options ---
    vendor_options = get_vendor_name_list(company_id)
    requester_options = get_requester_options(company_id)
    account_options = get_expense_asset_account_options(company_id)

    # --- Invoice list for optional linking ---
    inv_rows = list_invoices(company_id)
    idf = pd.DataFrame(inv_rows) if inv_rows else pd.DataFrame(
        columns=[
            "id",
            "invoice_number",
            "vendor_invoice_number",
            "vendor",
            "summary",
            "vatable_amount",
            "non_vatable_amount",
            "vat_rate",
            "wht_rate",
            "vat_amount",
            "wht_amount",
            "subtotal",
            "total_amount",
            "terms",
            "currency",
            "payable_account",
            "expense_asset_account",
            "file_name",
            "last_modified",
        ]
    )

    invoice_options = ["-- No Linked Invoice --"]
    if not idf.empty and "invoice_number" in idf.columns:
        invoice_options += idf["invoice_number"].astype(str).tolist()

    # ===================== VOUCHER FORM =====================
    with st.form("voucher_create_form"):
        # ---------- Header ----------
        h1, h2, h3 = st.columns(3)

        vendor = h1.selectbox(
            "Vendor",
            vendor_options if vendor_options else ["-- No vendors yet --"],
            index=0,
        )
        requester = h2.selectbox(
            "Requester",
            requester_options if requester_options else ["-- No requesters yet --"],
            index=0,
        )
        currency = h3.text_input("Currency", value="NGN")

        # Suggested voucher number (can override)
        suggested_vnum = generate_voucher_number(company_id)
        voucher_number = st.text_input(
            "Voucher Number",
            value=suggested_vnum,
            help="You can override this number or leave the suggested one.",
        )

        # ---------- Invoice linking ----------
        c_inv1, c_inv2 = st.columns([2, 3])
        invoice_choice = c_inv1.selectbox(
            "Linked Invoice (optional)",
            invoice_options,
            index=0,
        )
        manual_invoice_ref = c_inv2.text_input(
            "Manual Invoice Reference (if not in list)",
            value="",
        )

        selected_invoice = None
        if invoice_choice != "-- No Linked Invoice --" and not idf.empty:
            match = idf[idf["invoice_number"] == invoice_choice]
            if not match.empty:
                selected_invoice = match.iloc[0]

        if selected_invoice is not None:
            inv_cur = (selected_invoice.get("currency") or currency or "NGN").upper()
            total_amount = float(selected_invoice.get("total_amount") or 0.0)
            st.info(
                f"Linked to invoice {selected_invoice['invoice_number']} "
                f"({selected_invoice['vendor']} • "
                f"TOTAL {money(total_amount, inv_cur)})"
            )

        # ---------- Line items ----------
        st.markdown("### Voucher Lines")

        num_lines = st.number_input(
            "Number of lines",
            min_value=1,
            max_value=20,
            value=1,
            step=1,
        )

        lines: List[Dict[str, Any]] = []
        total_payable = 0.0

        for idx in range(int(num_lines)):
            st.markdown(f"**Line {idx + 1}**")
            lc1, lc2, lc3, lc4, lc5 = st.columns([3, 2, 3, 2, 2])

            desc = lc1.text_input(
                "Description",
                key=f"v_line_desc_{idx}",
            )
            amt = lc2.number_input(
                "Amount",
                min_value=0.00,
                format="%.2f",
                key=f"v_line_amt_{idx}",
            )
            acct = lc3.selectbox(
                "Expense or Asset Account",
                account_options if account_options else ["-- No accounts yet --"],
                key=f"v_line_acct_{idx}",
            )
            vat_percent = lc4.number_input(
                "VAT %",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                key=f"v_line_vat_{idx}",
            )
            wht_percent = lc5.number_input(
                "WHT %",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                key=f"v_line_wht_{idx}",
            )

            vat_val = amt * vat_percent / 100.0
            wht_val = amt * wht_percent / 100.0
            total_val = amt + vat_val - wht_val
            total_payable += float(total_val)

            st.caption(
                f"VAT: {money(vat_val, currency)} • "
                f"WHT: {money(wht_val, currency)} • "
                f"PAYABLE: {money(total_val, currency)}"
            )

            lines.append(
                {
                    "description": desc,
                    "account_name": acct,
                    "amount": float(amt),
                    "vat_percent": float(vat_percent),
                    "wht_percent": float(wht_percent),
                    "vat_value": float(vat_val),
                    "wht_value": float(wht_val),
                    "total": float(total_val),
                }
            )

            st.markdown("---")

        st.markdown(
            f"**Total Payable (all lines):** {money(total_payable, currency)}"
        )

        # ---------- Attachment ----------
        uploaded_file = st.file_uploader(
            "Attach Voucher Document (PDF/JPG/PNG)",
            type=["pdf", "jpg", "jpeg", "png"],
        )

        # ---------- Submit ----------
        submitted = st.form_submit_button("Save Voucher")

    # ===================== SUBMIT HANDLER =====================
    if submitted:
        # Decide final invoice_ref
        if invoice_choice != "-- No Linked Invoice --":
            final_invoice_ref = invoice_choice
        else:
            final_invoice_ref = manual_invoice_ref.strip()

        if not vendor_options:
            st.error("You must create at least one vendor in CRM before creating vouchers.")
            return

        if not requester_options:
            st.error("You must create at least one requester/staff in CRM before creating vouchers.")
            return

        # Basic validation of lines
        valid_lines = [ln for ln in lines if ln["amount"] > 0]
        if not valid_lines:
            st.error("Please enter at least one line with a positive amount.")
            return

        file_name = None
        file_bytes = None
        if uploaded_file is not None:
            file_name = uploaded_file.name
            file_bytes = uploaded_file.read()

        err = create_voucher(
            company_id=company_id,
            username=username,
            vendor=str(vendor),
            requester=str(requester),
            invoice_ref=final_invoice_ref,
            currency=currency,
            lines=valid_lines,
            file_name=file_name,
            file_bytes=file_bytes,
            voucher_number=voucher_number,
        )
        if err:
            st.error(err)
        else:
            st.success("Voucher created successfully.")
            rerun()

    # ===================== RECENT VOUCHERS TABLE =====================
    st.markdown("---")
    st.subheader("Recent Vouchers")

    vouchers = list_vouchers(company_id)
    if vouchers:
        vdf = pd.DataFrame(vouchers)
        show_cols = [
            "voucher_number",
            "vendor",
            "requester",
            "invoice_ref",
            "currency",
            "status",
            "created_at",
            "last_modified",
        ]
        show_cols = [c for c in show_cols if c in vdf.columns]
        st.dataframe(
            vdf[show_cols].sort_values("id", ascending=False).head(20),
            use_container_width=True,
        )
    else:
        st.info("No vouchers yet.")

    st.markdown("</div>", unsafe_allow_html=True)


# =====================================================================
# TAB: REPORTS – Consolidated, cleaner logic
# =====================================================================

def app_reports():
    require_login()
    user = current_user()
    if not user:
        return

    company_id = user["company_id"]

    st.markdown(
        "<div class='card'><div class='card-header'>📊 Complete Financial Reports</div>",
        unsafe_allow_html=True,
    )

    # ---------------------------------------------------
    # Load base data
    # ---------------------------------------------------
    vouchers = list_vouchers(company_id)
    invoices = list_invoices(company_id)

    vdf = pd.DataFrame(vouchers) if vouchers else pd.DataFrame(
        columns=[
            "id",
            "voucher_number",
            "vendor",
            "requester",
            "invoice_ref",
            "currency",
            "status",
            "parent_id",
            "version",
            "file_name",
            "created_at",
            "last_modified",
            "approved_by",
            "approved_at",
        ]
    )
    idf = pd.DataFrame(invoices) if invoices else pd.DataFrame(
        columns=[
            "id",
            "invoice_number",
            "vendor_invoice_number",
            "vendor",
            "summary",
            "vatable_amount",
            "non_vatable_amount",
            "vat_rate",
            "wht_rate",
            "vat_amount",
            "wht_amount",
            "subtotal",
            "total_amount",
            "terms",
            "payable_account",
            "expense_asset_account",
            "currency",
            "file_name",
            "last_modified",
        ]
    )

    # Helper: load lines for one voucher
    def get_lines_for_voucher_df(voucher_id: int) -> pd.DataFrame:
        rows = list_voucher_lines(company_id, voucher_id)
        if not rows:
            return pd.DataFrame(
                columns=[
                    "id",
                    "voucher_id",
                    "line_no",
                    "description",
                    "account_name",
                    "amount",
                    "vat_percent",
                    "wht_percent",
                    "vat_value",
                    "wht_value",
                    "total",
                ]
            )
        return pd.DataFrame(rows)

    # ---------------------------------------------------
    # Build Invoice Summary
    # ---------------------------------------------------
    invoice_rows: List[Dict[str, Any]] = []

    if not idf.empty:
        for _, inv in idf.iterrows():
            inv_num = inv["invoice_number"]
            try:
                gross = float(inv.get("total_amount") or 0.0)
                vatable = float(inv.get("vatable_amount") or 0.0)
                vat_amt = float(inv.get("vat_amount") or 0.0)
                wht_amt = float(inv.get("wht_amount") or 0.0)
                non_vatable = float(inv.get("non_vatable_amount") or 0.0)
                payable = float(inv.get("subtotal") or 0.0)
            except Exception:
                gross = vatable = vat_amt = wht_amt = non_vatable = payable = 0.0

            # All vouchers linked to this invoice
            linked_vouchers = vdf[vdf.get("invoice_ref", "") == inv_num]

            paid_gross = 0.0
            paid_payable = 0.0
            v_count = 0

            for _, v in linked_vouchers.iterrows():
                v_id = int(v["id"])
                v_lines = get_lines_for_voucher_df(v_id)

                if v_lines.empty:
                    continue

                try:
                    v_gross = float(
                        (
                            v_lines.get("amount", 0.0)
                            + v_lines.get("vat_value", 0.0)
                            + v_lines.get("wht_value", 0.0)
                        ).sum()
                    )
                except Exception:
                    v_gross = 0.0

                try:
                    v_payable_sum = float(
                        (
                            v_lines.get("amount", 0.0)
                            + v_lines.get("vat_value", 0.0)
                            - v_lines.get("wht_value", 0.0)
                        ).sum()
                    )
                except Exception:
                    v_payable_sum = 0.0

                paid_gross += v_gross
                paid_payable += v_payable_sum
                v_count += 1

            remaining_gross = max(0.0, gross - paid_gross)
            remaining_payable = max(0.0, payable - paid_payable)

            status = "Unpaid"
            if remaining_gross <= 1e-6 and gross > 0:
                status = "Fully Paid"
            elif paid_gross > 0:
                status = "Partially Paid"

            inv_currency = inv.get("currency") or "NGN"

            invoice_rows.append(
                {
                    "Invoice No": inv_num,
                    "Vendor": inv.get("vendor"),
                    "Currency": inv_currency,
                    "Vendor Inv No": inv.get("vendor_invoice_number"),
                    "Summary": inv.get("summary"),
                    "VAT Rate %": float(inv.get("vat_rate") or 0.0),
                    "WHT Rate %": float(inv.get("wht_rate") or 0.0),
                    "Vatable": vatable,
                    "VAT": vat_amt,
                    "WHT": wht_amt,
                    "Non-Vatable": non_vatable,
                    "Total Payable (Invoice)": payable,
                    "Gross Invoice": gross,
                    "Paid (Gross)": paid_gross,
                    "Paid (Payable)": paid_payable,
                    "Remaining (Gross)": remaining_gross,
                    "Remaining (Payable)": remaining_payable,
                    "Voucher Count": v_count,
                    "Payable Account": inv.get("payable_account", "Accounts Payable"),
                    "Expense/Asset Account": inv.get(
                        "expense_asset_account", "Expense"
                    ),
                    "Terms": inv.get("terms"),
                    "Last Modified": inv.get("last_modified"),
                    "Status": status,
                }
            )

    df_invoices = pd.DataFrame(invoice_rows)

    if "Currency" not in df_invoices.columns:
        df_invoices["Currency"] = "NGN"

    df_invoices_ngn = df_invoices[
        df_invoices["Currency"].fillna("NGN").str.upper() == "NGN"
    ]
    df_invoices_fx = df_invoices[
        df_invoices["Currency"].fillna("NGN").str.upper() != "NGN"
    ]

    # ---------------------------------------------------
    # Build Voucher Summary + voucher currency map
    # ---------------------------------------------------
    voucher_rows: List[Dict[str, Any]] = []
    voucher_currency_cache: Dict[int, str] = {}

    if not vdf.empty:
        for _, v in vdf.iterrows():
            vid = int(v["id"])
            v_currency = (v.get("currency") or "NGN").upper()

            # If linked invoice has a currency, prefer that
            try:
                inv_ref = v.get("invoice_ref")
                if isinstance(inv_ref, str) and not idf.empty:
                    match = idf[idf["invoice_number"] == inv_ref]
                    if not match.empty:
                        v_currency = (
                            match.iloc[0].get("currency") or v_currency or "NGN"
                        )
            except Exception:
                pass

            voucher_currency_cache[vid] = v_currency

            lines_df = get_lines_for_voucher_df(vid)

            try:
                v_payable = float(
                    (
                        lines_df.get("amount", 0.0)
                        + lines_df.get("vat_value", 0.0)
                        - lines_df.get("wht_value", 0.0)
                    ).sum()
                )
            except Exception:
                v_payable = 0.0

            try:
                v_gross = float(
                    (
                        lines_df.get("amount", 0.0)
                        + lines_df.get("vat_value", 0.0)
                        + lines_df.get("wht_value", 0.0)
                    ).sum()
                )
            except Exception:
                v_gross = 0.0

            voucher_rows.append(
                {
                    "Voucher No": v.get("voucher_number") or f"V{vid}",
                    "Voucher Id": vid,
                    "Parent Id": v.get("parent_id"),
                    "Version": v.get("version"),
                    "Vendor": v.get("vendor"),
                    "Requester": v.get("requester"),
                    "Linked Invoice": v.get("invoice_ref") or "",
                    "Voucher Status": v.get("status") or "",
                    "Currency": v_currency,
                    "Payable (Voucher)": v_payable,
                    "Gross (Voucher)": v_gross,
                    "File Name": v.get("file_name") or "",
                    "Last Modified": v.get("last_modified"),
                }
            )

    df_vouchers = pd.DataFrame(voucher_rows)

    # ---------------------------------------------------
    # Build Line Items (flat)
    # ---------------------------------------------------
    line_rows: List[Dict[str, Any]] = []

    if not vdf.empty:
        for _, v in vdf.iterrows():
            vid = int(v["id"])
            v_currency = voucher_currency_cache.get(vid, "NGN")
            lines_df = get_lines_for_voucher_df(vid)

            if lines_df.empty:
                continue

            for _, ln in lines_df.iterrows():
                line_rows.append(
                    {
                        "Voucher No": v.get("voucher_number") or f"V{vid}",
                        "Linked Invoice": v.get("invoice_ref") or "",
                        "Currency": v_currency,
                        "Description": ln.get("description"),
                        "Amount": float(ln.get("amount") or 0.0),
                        "Expense/Asset Account": ln.get("account_name"),
                        "VAT %": float(ln.get("vat_percent") or 0.0),
                        "WHT %": float(ln.get("wht_percent") or 0.0),
                        "VAT Value": float(ln.get("vat_value") or 0.0),
                        "WHT Value": float(ln.get("wht_value") or 0.0),
                        "Line Total (Payable)": float(ln.get("total") or 0.0),
                    }
                )

    df_lines = pd.DataFrame(line_rows)

    # ---------------------------------------------------
    # Build General Journal (Invoices + Vouchers)
    # ---------------------------------------------------
    journal_rows: List[Dict[str, Any]] = []

    # Invoices DR/CR
    for _, inv in idf.iterrows():
        inv_num = inv.get("invoice_number")
        summary = inv.get("summary") or ""
        inv_currency = (inv.get("currency") or "NGN").upper()
        vatable = float(inv.get("vatable_amount") or 0.0)
        non_vatable = float(inv.get("non_vatable_amount") or 0.0)
        vat_amt = float(inv.get("vat_amount") or 0.0)
        pay_acc = inv.get("payable_account") or "Accounts Payable"
        exp_asset_acc = inv.get("expense_asset_account") or "Expense"

        invoice_drcr_total = vatable + non_vatable + vat_amt
        if invoice_drcr_total <= 0:
            continue

        # DR expense/asset (vatable + non-vatable + VAT)
        journal_rows.append(
            {
                "Date": inv.get("last_modified"),
                "Type": "INVOICE",
                "Invoice No": inv_num,
                "Voucher No": "",
                "Description": f"{summary} (DR expense/asset + VAT + non-vatable)",
                "Currency": inv_currency,
                "DR Account": exp_asset_acc,
                "DR Amount": invoice_drcr_total,
                "CR Account": "",
                "CR Amount": 0.0,
            }
        )

        # CR accounts payable
        journal_rows.append(
            {
                "Date": inv.get("last_modified"),
                "Type": "INVOICE",
                "Invoice No": inv_num,
                "Voucher No": "",
                "Description": f"{summary} (CR accounts payable)",
                "Currency": inv_currency,
                "DR Account": "",
                "DR Amount": 0.0,
                "CR Account": pay_acc,
                "CR Amount": invoice_drcr_total,
            }
        )

    # Vouchers postings
    for _, v in vdf.iterrows():
        vnum = v.get("voucher_number") or f"V{v['id']}"
        lines_df = get_lines_for_voucher_df(int(v["id"]))
        if lines_df.empty:
            continue

        linked_inv = None
        if v.get("invoice_ref"):
            match = idf[idf["invoice_number"] == v.get("invoice_ref")]
            if not match.empty:
                linked_inv = match.iloc[0]

        if linked_inv is not None:
            pay_acc = linked_inv.get("payable_account") or "Accounts Payable"
            inv_num = linked_inv.get("invoice_number")
            v_currency = (linked_inv.get("currency") or "NGN").upper()
            for _, ln in lines_df.iterrows():
                amt = float(ln.get("amount") or 0.0)
                vat_val = float(ln.get("vat_value") or 0.0)
                wht_val = float(ln.get("wht_value") or 0.0)
                total_no_wht = amt + vat_val

                if total_no_wht > 0:
                    # DR payable (amt + VAT)
                    journal_rows.append(
                        {
                            "Date": v.get("last_modified"),
                            "Type": "VOUCHER",
                            "Invoice No": inv_num,
                            "Voucher No": vnum,
                            "Description": f"{ln.get('description')} (DR payable: amt+VAT)",
                            "Currency": v_currency,
                            "DR Account": pay_acc,
                            "DR Amount": total_no_wht,
                            "CR Account": "",
                            "CR Amount": 0.0,
                        }
                    )
                    # CR suspense (amt + VAT)
                    journal_rows.append(
                        {
                            "Date": v.get("last_modified"),
                            "Type": "VOUCHER",
                            "Invoice No": inv_num,
                            "Voucher No": vnum,
                            "Description": f"{ln.get('description')} (CR suspense: amt+VAT)",
                            "Currency": v_currency,
                            "DR Account": "",
                            "DR Amount": 0.0,
                            "CR Account": "Suspense Account",
                            "CR Amount": total_no_wht,
                        }
                    )

                if wht_val > 0:
                    # DR payable (WHT)
                    journal_rows.append(
                        {
                            "Date": v.get("last_modified"),
                            "Type": "VOUCHER",
                            "Invoice No": inv_num,
                            "Voucher No": vnum,
                            "Description": f"{ln.get('description')} (DR payable: WHT)",
                            "Currency": v_currency,
                            "DR Account": pay_acc,
                            "DR Amount": wht_val,
                            "CR Account": "",
                            "CR Amount": 0.0,
                        }
                    )
                    # CR WHT Payable
                    journal_rows.append(
                        {
                            "Date": v.get("last_modified"),
                            "Type": "VOUCHER",
                            "Invoice No": inv_num,
                            "Voucher No": vnum,
                            "Description": f"{ln.get('description')} (CR WHT payable)",
                            "Currency": v_currency,
                            "DR Account": "",
                            "DR Amount": 0.0,
                            "CR Account": "WHT Payable",
                            "CR Amount": wht_val,
                        }
                    )
        else:
            # Vouchers not linked to any invoice: treat as direct expense
            v_currency = (v.get("currency") or "NGN").upper()
            for _, ln in lines_df.iterrows():
                amt = float(ln.get("amount") or 0.0)
                vat_val = float(ln.get("vat_value") or 0.0)
                wht_val = float(ln.get("wht_value") or 0.0)
                line_payable = amt + vat_val - wht_val
                if line_payable <= 0:
                    continue

                # DR expense/asset
                journal_rows.append(
                    {
                        "Date": v.get("last_modified"),
                        "Type": "VOUCHER",
                        "Invoice No": "",
                        "Voucher No": vnum,
                        "Description": f"{ln.get('description')} (DR expense/asset)",
                        "Currency": v_currency,
                        "DR Account": ln.get("account_name"),
                        "DR Amount": line_payable,
                        "CR Account": "",
                        "CR Amount": 0.0,
                    }
                )
                # CR suspense
                journal_rows.append(
                    {
                        "Date": v.get("last_modified"),
                        "Type": "VOUCHER",
                        "Invoice No": "",
                        "Voucher No": vnum,
                        "Description": f"{ln.get('description')} (CR suspense)",
                        "Currency": v_currency,
                        "DR Account": "",
                        "DR Amount": 0.0,
                        "CR Account": "Suspense Account",
                        "CR Amount": line_payable,
                    }
                )

    df_journal = pd.DataFrame(journal_rows)

    # ---------------------------------------------------
    # Audit log
    # ---------------------------------------------------
    try:
        with closing(connect()) as conn:
            df_audit = pd.read_sql_query(
                """
                SELECT
                    ts   AS "Timestamp",
                    user AS "User",
                    action AS "Action",
                    entity AS "Entity",
                    ref AS "Reference",
                    details AS "Details"
                FROM audit_log
                WHERE company_id = %s
                ORDER BY id DESC
                """,
                conn,
                params=(company_id,),
            )
    except Exception:
        df_audit = pd.DataFrame(
            columns=["Timestamp", "User", "Action", "Entity", "Reference", "Details"]
        )

    # ---------------------------------------------------
    # TABS for displaying everything
    # ---------------------------------------------------
    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "🧾 Invoice Summary",
            "📑 Voucher Summary",
            "🧰 Line Items",
            "📚 General Journal",
            "📂 Audit & Excel Export",
        ]
    )

    # ----- Invoice Summary -----
    with tab1:
        if df_invoices.empty:
            st.info("No invoices yet.")
        else:
            if not df_invoices_ngn.empty:
                st.markdown("**Invoices (NGN Only)**")
                st.dataframe(
                    df_invoices_ngn.style.format(
                        {
                            "Vatable": "{:,.2f}",
                            "VAT": "{:,.2f}",
                            "WHT": "{:,.2f}",
                            "Non-Vatable": "{:,.2f}",
                            "Total Payable (Invoice)": "{:,.2f}",
                            "Gross Invoice": "{:,.2f}",
                            "Paid (Gross)": "{:,.2f}",
                            "Paid (Payable)": "{:,.2f}",
                            "Remaining (Gross)": "{:,.2f}",
                            "Remaining (Payable)": "{:,.2f}",
                        }
                    ),
                    use_container_width=True,
                )
            else:
                st.info("No NGN invoices yet.")

            if not df_invoices_fx.empty:
                st.markdown("**Invoices (Multi-Currency – Non-NGN)**")
                st.dataframe(
                    df_invoices_fx.style.format(
                        {
                            "Vatable": "{:,.2f}",
                            "VAT": "{:,.2f}",
                            "WHT": "{:,.2f}",
                            "Non-Vatable": "{:,.2f}",
                            "Total Payable (Invoice)": "{:,.2f}",
                            "Gross Invoice": "{:,.2f}",
                            "Paid (Gross)": "{:,.2f}",
                            "Paid (Payable)": "{:,.2f}",
                            "Remaining (Gross)": "{:,.2f}",
                            "Remaining (Payable)": "{:,.2f}",
                        }
                    ),
                    use_container_width=True,
                )
            else:
                st.info("No non-NGN (multi-currency) invoices yet.")

            if "Currency" in df_invoices.columns:
                currency_summary = (
                    df_invoices.groupby("Currency")[
                        [
                            "Vatable",
                            "VAT",
                            "WHT",
                            "Non-Vatable",
                            "Total Payable (Invoice)",
                            "Gross Invoice",
                            "Paid (Gross)",
                            "Paid (Payable)",
                            "Remaining (Gross)",
                            "Remaining (Payable)",
                        ]
                    ]
                    .sum()
                    .reset_index()
                )
                st.markdown("**Totals by Currency (All Invoices)**")
                st.dataframe(currency_summary, use_container_width=True)

    # ----- Voucher Summary -----
    with tab2:
        if df_vouchers.empty:
            st.info("No vouchers yet.")
        else:
            st.dataframe(
                df_vouchers.style.format(
                    {
                        "Payable (Voucher)": "{:,.2f}",
                        "Gross (Voucher)": "{:,.2f}",
                    }
                ),
                use_container_width=True,
            )

    # ----- Line Items -----
    with tab3:
        if df_lines.empty:
            st.info("No voucher line items yet.")
        else:
            st.dataframe(
                df_lines.style.format(
                    {
                        "Amount": "{:,.2f}",
                        "VAT Value": "{:,.2f}",
                        "WHT Value": "{:,.2f}",
                        "Line Total (Payable)": "{:,.2f}",
                    }
                ),
                use_container_width=True,
            )

    # ----- General Journal -----
    with tab4:
        if df_journal.empty:
            st.info("No journal entries yet.")
        else:
            st.dataframe(
                df_journal.style.format(
                    {
                        "DR Amount": "{:,.2f}",
                        "CR Amount": "{:,.2f}",
                    }
                ),
                use_container_width=True,
            )

    # ----- Audit & Excel Export -----
    with tab5:
        st.markdown("### Audit Log")
        if df_audit.empty:
            st.info("No activities logged yet.")
        else:
            st.dataframe(df_audit, use_container_width=True)

        st.markdown("---")
        st.markdown("### Download Combined Excel")

        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df_invoices.to_excel(writer, sheet_name="Invoices", index=False)
            df_vouchers.to_excel(writer, sheet_name="Vouchers", index=False)
            df_lines.to_excel(writer, sheet_name="Lines", index=False)
            df_journal.to_excel(writer, sheet_name="Journal", index=False)
            df_audit.to_excel(writer, sheet_name="Audit", index=False)
        output.seek(0)

        st.download_button(
            "Download Financial Reports (Excel)",
            data=output,
            file_name="financial_reports.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.markdown("</div>", unsafe_allow_html=True)
