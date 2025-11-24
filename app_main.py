# app_main.py
# Main Streamlit app wiring all modules together, with multi-tenant support

import streamlit as st
import pandas as pd
import psycopg2

from typing import List, Dict, Any, Optional
from contextlib import closing

from db_config import init_schema, connect
from auth_module import (
    init_auth,
    require_login,
    require_admin,
    require_permission,
    current_user,
)
from crm_gateway import (
    init_crm_schema,
    get_vendor_name_list,
    get_requester_options,
    get_expense_asset_account_options,
    list_vendors,
    upsert_vendor,
    delete_vendor,
    list_staff,
    upsert_staff,
    delete_staff,
)
from invoices_module import (
    init_invoice_schema,
    list_invoices,
    update_invoice,
    delete_invoice,
    compute_invoice_totals,
)
from vouchers_module import (
    init_voucher_schema,
    list_vouchers,
    list_voucher_lines,
    update_voucher,
)
from pdf_utils import build_voucher_pdf_bytes

# ------------------------
# Helpers
# ------------------------

try:
    import reportlab  # noqa: F401
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False


def money(value: float, currency: str = "NGN") -> str:
    try:
        v = float(value or 0.0)
    except Exception:
        v = 0.0
    # Simple formatting without localisation
    return f"{currency} {v:,.2f}"


def safe_index(options: List[Any], value: Any) -> int:
    """Return index of value in options, or 0 if not found."""
    try:
        return options.index(value)
    except Exception:
        return 0


def embed_file(name: str, data: bytes) -> None:
    """Show a simple download button for a file attachment."""
    if not data:
        return
    st.download_button(
        label=f"Download {name}",
        data=data,
        file_name=name,
        key=f"dl_{name}",
    )


def rerun() -> None:
    try:
        st.experimental_rerun()
    except Exception:
        st.rerun()


# ------------------------
# Vouchers tab: All Vouchers (list + edit + attachments + PDF)
# ------------------------

def render_all_vouchers_tab() -> None:
    user = current_user()
    if not user:
        require_login()
        return

    company_id = user["company_id"]
    username = user["username"]

    # Dropdown source data
    vendors = get_vendor_name_list(company_id)
    requesters = get_requester_options(company_id)
    line_accounts = get_expense_asset_account_options(company_id)

    # Invoice list (for linking)
    inv_rows = list_invoices(company_id)
    current_idf = pd.DataFrame(inv_rows) if inv_rows else pd.DataFrame(
        columns=[
            "id",
            "parent_id",
            "version",
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
    invoice_numbers = ["-- None --"] + current_idf.get("invoice_number", pd.Series([], dtype=str)).tolist()

    vouchers = list_vouchers(company_id)
    current_vdf = pd.DataFrame(vouchers) if vouchers else pd.DataFrame(
        columns=[
            "id",
            "parent_id",
            "version",
            "voucher_number",
            "vendor",
            "requester",
            "invoice_ref",
            "currency",
            "status",
            "created_at",
            "last_modified",
            "approved_by",
            "approved_at",
        ]
    )

    st.markdown(
        "<div class='card'><div class='card-header'>📑 All Vouchers</div>",
        unsafe_allow_html=True,
    )

    if current_vdf.empty:
        st.info("No vouchers yet.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    for _, v in current_vdf.iterrows():
        voucher_id = int(v["id"])
        parent_id = v.get("parent_id")

        # Get all lines for this voucher
        lines = list_voucher_lines(company_id, voucher_id)
        lines_df = pd.DataFrame(lines) if lines else pd.DataFrame(
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

        # Default voucher currency
        voucher_currency = v.get("currency") or "NGN"

        # If linked to an invoice, try to infer currency from invoice row
        try:
            inv_ref = v.get("invoice_ref")
            if isinstance(inv_ref, str) and not current_idf.empty:
                match = current_idf[current_idf["invoice_number"] == inv_ref]
                if not match.empty:
                    voucher_currency = match.iloc[0].get("currency", voucher_currency) or voucher_currency
        except Exception:
            pass

        # Compute total payable from lines
        total_payable_v = float(
            (lines_df.get("amount", 0.0) + lines_df.get("vat_value", 0.0) - lines_df.get("wht_value", 0.0)).sum()
        )

        display_vnum = v.get("voucher_number") or str(voucher_id)

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

            for idx, ln in lines_df.iterrows():
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
                        key=f"ldesc_{voucher_id}_{idx}",
                    )
                    amt = lc[1].number_input(
                        "Amt",
                        value=amt_default,
                        min_value=0.00,
                        format="%.2f",
                        key=f"lamt_{voucher_id}_{idx}",
                    )
                    vat = lc[2].number_input(
                        "VAT%",
                        value=vat_default,
                        key=f"lvat_{voucher_id}_{idx}",
                    )
                    wht = lc[3].number_input(
                        "WHT%",
                        value=wht_default,
                        key=f"lwht_{voucher_id}_{idx}",
                    )
                    acct_val = acct_default
                else:
                    lc = st.columns([3, 2, 3, 2, 2])
                    desc = lc[0].text_input(
                        "Desc",
                        descr_default,
                        key=f"ldesc_{voucher_id}_{idx}",
                    )
                    amt = lc[1].number_input(
                        "Amt",
                        value=amt_default,
                        min_value=0.00,
                        format="%.2f",
                        key=f"lamt_{voucher_id}_{idx}",
                    )
                    acct_val = lc[2].selectbox(
                        "Expense or Asset Account",
                        line_accounts,
                        index=safe_index(line_accounts, acct_default),
                        key=f"lacct_{voucher_id}_{idx}",
                    )
                    vat = lc[3].number_input(
                        "VAT%",
                        value=vat_default,
                        key=f"lvat_{voucher_id}_{idx}",
                    )
                    wht = lc[4].number_input(
                        "WHT%",
                        value=wht_default,
                        key=f"lwht_{voucher_id}_{idx}",
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
                        "description": desc,
                        "account_name": acct_val,
                        "amount": float(amt),
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
                                """
                                SELECT file_name, file_data
                                FROM voucher_documents
                                WHERE company_id = %s
                                  AND id = %s
                                """,
                                (company_id, sel_id),
                            )
                            rowd = cur2.fetchone()
                        if rowd and rowd[0] and rowd[1]:
                            embed_file(rowd[0], rowd[1])

                        if st.button(
                            "Delete Selected Attachment",
                            key=f"del_voucher_doc_{voucher_id}",
                        ):
                            try:
                                with closing(connect()) as conn, closing(conn.cursor()) as cur_del:
                                    cur_del.execute(
                                        "DELETE FROM voucher_documents WHERE company_id = %s AND id = %s",
                                        (company_id, sel_id),
                                    )
                                    conn.commit()
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

            new_attachments: List[Dict[str, Any]] = []
            if new_files:
                for f in new_files:
                    try:
                        fb = f.read()
                    except Exception:
                        fb = None
                    if not fb:
                        continue
                    new_attachments.append({"file_name": f.name, "file_data": fb})

            act_left, act_right = st.columns([1, 1])

            # ------- Save header+lines+new attachments -------
            if act_left.button("Save Changes", key=f"save_{voucher_id}"):
                err = update_voucher(
                    company_id=company_id,
                    voucher_id=voucher_id,
                    username=username,
                    vendor=str(new_vendor),
                    requester=str(new_requester),
                    invoice_ref=(new_invoice if new_invoice != "-- None --" else ""),
                    currency=voucher_currency,
                    lines=updated_lines,
                )
                if err:
                    st.error(err)
                else:
                    # Save new attachments (if any)
                    if new_attachments:
                        try:
                            with closing(connect()) as conn, closing(conn.cursor()) as cur:
                                for att in new_attachments:
                                    cur.execute(
                                        """
                                        INSERT INTO voucher_documents (
                                            company_id,
                                            voucher_id,
                                            file_name,
                                            file_data
                                        ) VALUES (%s, %s, %s, %s)
                                        ON CONFLICT (voucher_id, file_name) DO UPDATE
                                        SET file_data = EXCLUDED.file_data,
                                            uploaded_at = CURRENT_TIMESTAMP
                                        """,
                                        (
                                            company_id,
                                            voucher_id,
                                            att["file_name"],
                                            psycopg2.Binary(att["file_data"]),
                                        ),
                                    )
                                conn.commit()
                        except Exception as e:
                            st.error(f"Saved voucher but failed to save attachments: {e}")
                    st.success("Voucher updated.")
                    rerun()

            # ------- Download PDF (using pdf_utils) -------
            if REPORTLAB_OK:
                try:
                    pdf_bytes = build_voucher_pdf_bytes(company_id, voucher_id)
                    safe_vnum = "".join(
                        ch if ch.isalnum() or ch in ("-", "_") else "_"
                        for ch in str(display_vnum)
                    ) or "voucher"

                    act_right.download_button(
                        label="Download PDF",
                        data=pdf_bytes,
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


# ------------------------
# Invoices tab: All Invoices
# ------------------------

def render_all_invoices_tab() -> None:
    user = current_user()
    if not user:
        require_login()
        return
    company_id = user["company_id"]
    username = user["username"]

    st.markdown(
        "<div class='card'><div class='card-header'>📄 All Invoices</div>",
        unsafe_allow_html=True,
    )

    invoices = list_invoices(company_id)
    if not invoices:
        st.info("No invoices yet.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    vendor_names = get_vendor_name_list(company_id)
    payable_accounts = get_expense_asset_account_options(company_id)
    expense_accounts = get_expense_asset_account_options(company_id)

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
                    payable_accounts, inv.get("payable_account") or ""
                ),
                key=f"inv_pay_acct_{inv_id}",
            )
            new_expense_acct = c11.selectbox(
                "Expense/Asset Account",
                expense_accounts,
                index=safe_index(
                    expense_accounts, inv.get("expense_asset_account") or ""
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

            new_file = st.file_uploader(
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
                    invoice_id=inv_id,
                    vendor_invoice_number=new_vendor_inv,
                    vendor=new_vendor,
                    summary=new_summary,
                    vatable_amount=new_vatable,
                    vat_rate=new_vat_rate,
                    wht_rate=new_wht_rate,
                    non_vatable_amount=new_non_vatable,
                    terms=inv.get("terms"),
                    payable_account=new_payable_acct,
                    expense_asset_account=new_expense_acct,
                    currency=new_currency,
                    username=username,
                    file_name=file_name,
                    file_data=file_bytes,
                )
                if err:
                    st.error(err)
                else:
                    st.success("Invoice updated.")
                    rerun()

            if act_r.button("Delete Invoice", key=f"inv_delete_{inv_id}"):
                err = delete_invoice(company_id, inv_id, username)
                if err:
                    st.error(err)
                else:
                    st.success("Invoice deleted.")
                    rerun()

    st.markdown("</div>", unsafe_allow_html=True)


# ------------------------
# Vendors tab: All Vendors
# ------------------------

def render_all_vendors_tab() -> None:
    user = current_user()
    if not user:
        require_login()
        return
    company_id = user["company_id"]
    username = user["username"]

    st.markdown(
        "<div class='card'><div class='card-header'>🏢 All Vendors</div>",
        unsafe_allow_html=True,
    )

    vendors_list = list_vendors(company_id)
    if not vendors_list:
        st.info("No vendors yet. Create them in the CRM / Vendors form.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

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


# ------------------------
# Staff tab: All Staff
# ------------------------

def render_all_staff_tab() -> None:
    user = current_user()
    if not user:
        require_login()
        return
    company_id = user["company_id"]

    st.markdown(
        "<div class='card'><div class='card-header'>👤 All Staff</div>",
        unsafe_allow_html=True,
    )

    staff_list = list_staff(company_id)
    if not staff_list:
        st.info("No staff yet. Create them in the CRM / Staff form.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    status_options = ["active", "inactive", "on_leave", "other"]

    for stf in staff_list:
        sid = stf["id"]
        full_name = f"{stf.get('first_name') or ''} {stf.get('last_name') or ''}".strip()
        status = stf.get("status") or "active"
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
                status_options,
                index=safe_index(status_options, status),
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


# ------------------------
# Main app
# ------------------------

def main() -> None:
    st.set_page_config(page_title="FinanceApp", layout="wide")

    # Ensure DB schemas exist
    init_schema()
    init_auth()
    init_crm_schema()
    init_invoice_schema()
    init_voucher_schema()

    # Auth gate
    require_login()
    user = current_user()
    if not user:
        return

    st.sidebar.markdown("## Navigation")
    choice = st.sidebar.radio(
        "Go to",
        ["Vouchers", "Invoices", "Vendors", "Staff"],
    )

    if choice == "Vouchers":
        st.title("Vouchers")
        tabs = st.tabs(["All Vouchers"])
        with tabs[0]:
            render_all_vouchers_tab()

    elif choice == "Invoices":
        st.title("Invoices")
        tabs = st.tabs(["All Invoices"])
        with tabs[0]:
            render_all_invoices_tab()

    elif choice == "Vendors":
        st.title("Vendors")
        tabs = st.tabs(["All Vendors"])
        with tabs[0]:
            render_all_vendors_tab()

    elif choice == "Staff":
        st.title("Staff")
        tabs = st.tabs(["All Staff"])
        with tabs[0]:
            render_all_staff_tab()


if __name__ == "__main__":
    main()
