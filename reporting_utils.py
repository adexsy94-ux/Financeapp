# reporting_utils.py
# Formatting + Excel download helper

import base64
from io import BytesIO
from typing import Dict

import pandas as pd
import streamlit as st


def money(x) -> str:
    try:
        val = float(x)
    except Exception:
        return ""
    return f"{val:,.2f}"


def excel_download_link_multi(sheets: Dict[str, pd.DataFrame], filename: str = "export.xlsx") -> str:
    """
    Create a single Excel file with multiple sheets and return HTML download link.
    """
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)

    output.seek(0)
    b64 = base64.b64encode(output.read()).decode()
    href = f'<a href="data:application/octet-stream;base64,{b64}" download="{filename}">Download Excel</a>'
    return href
