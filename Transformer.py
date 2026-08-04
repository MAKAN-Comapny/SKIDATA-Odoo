"""
Transform Parking Transactions export into Odoo-ready invoice lines.

Steps:
  1. Load Parking-Transactions .xlsx from exports/
  2. Keep rows where Payment is not 0 / blank
  3. Map/rename columns to the Odoo structure
  4. Write result to Cleaned-Exports/ and clear exports/
"""
import os
import glob
import re

import pandas as pd

EXPORTS_DIR = "exports"
CLEANED_EXPORTS_DIR = "Cleaned-Exports"
PARKING_FILE_MARKER = "Parking-Transactions"

# Exact output column names (Odoo import structure)
OUT_PARTNER = "Partner"
OUT_INVOICE_BILL_DATE = "Invoice/Bill Date"
OUT_INVOICE_LABEL = "Invoice/label"
OUT_QUANTITY = "Quantity"
OUT_TAXES = "invoice Lines/Taxes"
OUT_IN_TIME = "In Time"
OUT_OUT_TIME = "Out Time"
OUT_TICKET_SEQUENCE = "Ticket Sequence"
OUT_ANALYTICS = "Invoice Lines/Analytics Distribution"
OUT_PLATE_NUMBER = "Plate Number"

FIXED_PARTNER = "SKIDATA"
FIXED_QUANTITY = 1
FIXED_TAXES = "Sales Tax"
FIXED_ANALYTICS = '{"paid parking":100}'

OUTPUT_COLUMNS = [
    OUT_PARTNER,
    OUT_INVOICE_BILL_DATE,
    OUT_INVOICE_LABEL,
    OUT_QUANTITY,
    OUT_TAXES,
    OUT_IN_TIME,
    OUT_OUT_TIME,
    OUT_TICKET_SEQUENCE,
    OUT_ANALYTICS,
    OUT_PLATE_NUMBER,
]


def _normalize_header(name: str) -> str:
    """Lowercase and collapse separators for fuzzy column matching."""
    return re.sub(r"[\s/_-]+", "", str(name).strip().lower())


def _find_column(df: pd.DataFrame, *candidates: str) -> str:
    """Return the actual DataFrame column matching any candidate name."""
    normalized = {_normalize_header(c): c for c in df.columns}
    for candidate in candidates:
        key = _normalize_header(candidate)
        if key in normalized:
            return normalized[key]
    raise KeyError(
        f"Could not find column matching any of {candidates}. "
        f"Available columns: {list(df.columns)}"
    )


def _is_blank(value) -> bool:
    """Treat empty / NaN / SKIDATA plate placeholders (-, #) as missing."""
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    text = str(value).strip()
    return text == "" or text.lower() == "nan" or text in ("-", "#")


def _pick_plate(entry_plate, exit_plate) -> str:
    """Use whichever plate exists; if both exist take either; else blank."""
    entry_blank = _is_blank(entry_plate)
    exit_blank = _is_blank(exit_plate)
    if not entry_blank:
        return str(entry_plate).strip()
    if not exit_blank:
        return str(exit_plate).strip()
    return ""


def _payment_is_nonzero(series: pd.Series) -> pd.Series:
    """True where Payment has a numeric value other than 0."""
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.notna() & (numeric != 0)


def transform_parking_file(src_path: str, dest_path: str) -> tuple[int, int]:
    """
    Filter + remap one Parking Transactions workbook.
    Returns (rows_before_filter, rows_after_filter).
    """
    df = pd.read_excel(src_path)
    rows_before = len(df)

    payment_col = _find_column(df, "Payment")
    txn_date_col = _find_column(
        df, "Transaction Date/Time", "Transaction Date / Time", "Transaction Date Time"
    )
    article_col = _find_column(df, "Article")
    entry_date_col = _find_column(
        df, "Entry Date / Time", "Entry Date/Time", "Entry Date Time"
    )
    exit_date_col = _find_column(
        df, "Exit Date / Time", "Exit Date/Time", "Exit Date Time"
    )
    serial_col = _find_column(df, "Serial Number", "SerialNumber")
    entry_plate_col = _find_column(
        df, "Entry Plate Number", "Entry Plate", "EntryPlateNumber"
    )
    exit_plate_col = _find_column(
        df, "Exit Plate Number", "Exit Plate", "ExitPlateNumber"
    )

    filtered = df[_payment_is_nonzero(df[payment_col])].copy()
    rows_after = len(filtered)

    out = pd.DataFrame(
        {
            OUT_PARTNER: FIXED_PARTNER,
            OUT_INVOICE_BILL_DATE: filtered[txn_date_col].values,
            OUT_INVOICE_LABEL: filtered[article_col].values,
            OUT_QUANTITY: FIXED_QUANTITY,
            OUT_TAXES: FIXED_TAXES,
            OUT_IN_TIME: filtered[entry_date_col].values,
            OUT_OUT_TIME: filtered[exit_date_col].values,
            OUT_TICKET_SEQUENCE: filtered[serial_col].values,
            OUT_ANALYTICS: FIXED_ANALYTICS,
            OUT_PLATE_NUMBER: [
                _pick_plate(e, x)
                for e, x in zip(
                    filtered[entry_plate_col].values,
                    filtered[exit_plate_col].values,
                )
            ],
        }
    )
    out = out[OUTPUT_COLUMNS]

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    out.to_excel(dest_path, index=False)
    return rows_before, rows_after


def run():
    """Transform Parking Transactions files into Odoo format under Cleaned-Exports/."""
    all_files = glob.glob(os.path.join(EXPORTS_DIR, "*.xlsx"))
    parking_files = [f for f in all_files if PARKING_FILE_MARKER in os.path.basename(f)]

    if not parking_files:
        raise SystemExit(
            f"No Parking Transactions .xlsx files found in '{EXPORTS_DIR}'. "
            "Run scraper first."
        )

    os.makedirs(CLEANED_EXPORTS_DIR, exist_ok=True)

    for src in parking_files:
        base = os.path.basename(src)
        # Keep date prefix; mark as Odoo-ready output
        if base.lower().endswith(".xlsx"):
            dest_name = base[:-5] + "-Odoo.xlsx"
        else:
            dest_name = base + "-Odoo.xlsx"
        dest = os.path.join(CLEANED_EXPORTS_DIR, dest_name)

        before, after = transform_parking_file(src, dest)
        print(f"  {base}")
        print(f"    rows: {before} -> {after} (Payment != 0)")
        print(f"    wrote: {dest_name}")

    for file in all_files:
        os.remove(file)

    print(f"Transformed {len(parking_files)} Parking Transactions file(s) to {CLEANED_EXPORTS_DIR}/")


if __name__ == "__main__":
    run()
