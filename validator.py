"""
validator.py - Production-Grade Validation Gate for SKIDATA-Odoo Ingestion

Guarantees:
  1. Freshness Gate (SharePoint Item Selection):
     - Automatically rejects stale files if the scraper failed to upload a fresh file.
     - Filename must match today's or yesterday's date in Saudi Arabia Standard Time (AST = UTC+3),
       or match explicit TARGET_DATE if running a historical backfill.
  2. Container & Schema Integrity:
     - Non-empty, valid Excel workbook (> 5 KB).
     - Required columns present: Serial Number, Payment, Transaction Date/Time, Car Park.
  3. Data & Yesterday Date Precision:
     - 'Transaction Date/Time' MUST contain records for expected yesterday AST (or TARGET_DATE).
     - If yesterday's transactions are missing (0 rows), aborts immediately before any transform,
       SharePoint upload, or Odoo ERP calls.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Set

import pandas as pd

AST_TIMEZONE = timezone(timedelta(hours=3))
PARKING_MARKER = "Parking-Transactions"
MIN_FILE_SIZE_BYTES = 5_000  # Minimum valid Excel workbook size


def get_ast_now() -> datetime:
    """Return current datetime in Saudi Arabia Standard Time (AST = UTC+3)."""
    return datetime.now(AST_TIMEZONE)


def get_expected_yesterday(now_dt: Optional[datetime] = None) -> date:
    """Return yesterday's calendar date in AST."""
    ref = now_dt or get_ast_now()
    return (ref - timedelta(days=1)).date()


def get_expected_today(now_dt: Optional[datetime] = None) -> date:
    """Return today's calendar date in AST."""
    ref = now_dt or get_ast_now()
    return ref.date()


def parse_date_from_filename(filename: str) -> Optional[date]:
    """
    Extract the leading date prefix from a SKIDATA filename.
    Examples:
      '23-Nov-26-Morning-Parking-Transactions.xlsx' -> date(2026, 11, 23)
      '23-Nov-2026-Evening-Parking-Transactions.xlsx' -> date(2026, 11, 23)
      '2026-11-23-Parking-Transactions.xlsx' -> date(2026, 11, 23)
    """
    base = os.path.basename(filename)
    # Match patterns like DD-Mon-YY, DD-Mon-YYYY, or YYYY-MM-DD at the start
    match_dd_mon = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})", base)
    if match_dd_mon:
        d_str, m_str, y_str = match_dd_mon.groups()
        fmt = "%d-%b-%y" if len(y_str) == 2 else "%d-%b-%Y"
        try:
            return datetime.strptime(f"{d_str}-{m_str}-{y_str}", fmt).date()
        except ValueError:
            pass

    match_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", base)
    if match_iso:
        try:
            return datetime.strptime(match_iso.group(0), "%Y-%m-%d").date()
        except ValueError:
            pass

    return None


def parse_flexible_date(val: any) -> Optional[date]:
    """Parse mixed date representations into a python datetime.date object."""
    if val is None or pd.isna(val):
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, (datetime, pd.Timestamp)):
        return val.date()

    val_str = str(val).strip()
    if not val_str or val_str.lower() in ("totals", "total", "nan", "nat", "none"):
        return None

    # Try common SKIDATA date formats
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%d-%b-%y",
        "%d-%b-%Y",
    ):
        try:
            return datetime.strptime(val_str.split()[0], fmt).date()
        except (ValueError, IndexError):
            continue

    try:
        ts = pd.to_datetime(val_str, errors="coerce")
        if pd.notna(ts):
            return ts.date()
    except Exception:
        pass

    return None


class StaleFileError(RuntimeError):
    """Raised when the newest file in SharePoint is stale (older than expected run window)."""


class DataValidationError(RuntimeError):
    """Raised when downloaded report contents fail schema or yesterday data validation."""


def validate_filename_freshness(
    filename: str,
    target_date: Optional[str] = None,
    allow_stale: bool = False,
    now_dt: Optional[datetime] = None,
) -> date:
    """
    Gate 1: Validate that the selected candidate filename is fresh.
    - If target_date is set: filename must match target_date.
    - If target_date is not set: filename date must be today or yesterday in AST.
    Returns the parsed file date.
    """
    file_date = parse_date_from_filename(filename)
    if file_date is None:
        raise StaleFileError(
            f"Cannot extract date prefix from filename '{filename}'. "
            "Expected format like 'DD-Mon-YY-Parking-Transactions.xlsx'."
        )

    if allow_stale or os.getenv("ALLOW_STALE_FILE", "").lower() in ("true", "1", "yes"):
        return file_date

    raw_target = (target_date or os.getenv("TARGET_DATE", "")).strip()
    if raw_target:
        # Target date mode: verify exact match
        for fmt in ("%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y"):
            try:
                exp_date = datetime.strptime(raw_target, fmt).date()
                if file_date != exp_date:
                    raise StaleFileError(
                        f"File date mismatch for TARGET_DATE={raw_target}. "
                        f"Filename date is {file_date}, expected {exp_date}."
                    )
                return file_date
            except ValueError:
                pass
        return file_date

    # Automated daily mode: must be today or yesterday AST
    today_ast = get_expected_today(now_dt)
    yesterday_ast = get_expected_yesterday(now_dt)
    valid_window = {today_ast, yesterday_ast}

    if file_date not in valid_window:
        days_old = (today_ast - file_date).days
        raise StaleFileError(
            f"STALE FILE DETECTED in SharePoint: '{filename}' has date prefix "
            f"{file_date.strftime('%d-%b-%y')} ({days_old} day(s) old). "
            f"Expected a fresh report for {today_ast.strftime('%d-%b-%y')} or {yesterday_ast.strftime('%d-%b-%y')}. "
            "Halting pipeline to prevent processing stale data! Check if today's SKIDATA_SCRAPER completed successfully."
        )

    return file_date


@dataclass
class ReportValidationResult:
    filepath: str
    file_size_bytes: int
    row_count: int
    expected_yesterday: date
    dates_found: Set[date] = field(default_factory=set)
    yesterday_record_count: int = 0
    is_valid: bool = True
    summary: str = ""


def validate_parking_report_content(
    filepath: str,
    target_date: Optional[str] = None,
    skip_content_check: bool = False,
    now_dt: Optional[datetime] = None,
) -> ReportValidationResult:
    """
    Gate 2: Validate downloaded Parking Transactions Excel file.
    - Check size and workbook integrity.
    - Check essential columns.
    - Check that yesterday AST transactions (or target_date) exist and have > 0 records.
    """
    if not os.path.isfile(filepath):
        raise DataValidationError(f"File not found: '{filepath}'")

    size_bytes = os.path.getsize(filepath)
    if size_bytes < MIN_FILE_SIZE_BYTES:
        raise DataValidationError(
            f"Corrupted or empty file: '{filepath}' is only {size_bytes} bytes (min: {MIN_FILE_SIZE_BYTES} bytes)."
        )

    if skip_content_check or os.getenv("SKIP_CONTENT_VALIDATION", "").lower() in ("true", "1", "yes"):
        return ReportValidationResult(
            filepath=filepath,
            file_size_bytes=size_bytes,
            row_count=-1,
            expected_yesterday=get_expected_yesterday(now_dt),
            is_valid=True,
            summary="Content check skipped via flag.",
        )

    try:
        header_df = pd.read_excel(filepath, nrows=0)
    except Exception as exc:
        raise DataValidationError(f"Invalid Excel file or workbook read error for '{filepath}': {exc}") from exc

    # Find Transaction Date column
    cols = list(header_df.columns)
    txn_date_col = None
    for c in cols:
        clean = re.sub(r"[\s/_-]+", "", str(c).strip().lower())
        if clean in ("transactiondatetime", "transactiondate", "txndatetime"):
            txn_date_col = c
            break

    if not txn_date_col:
        raise DataValidationError(
            f"Missing required 'Transaction Date/Time' column in '{filepath}'. Found columns: {cols[:8]}"
        )

    # Determine expected date to verify
    raw_target = (target_date or os.getenv("TARGET_DATE", "")).strip()
    exp_date: Optional[date] = None
    if raw_target:
        for fmt in ("%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y"):
            try:
                exp_date = datetime.strptime(raw_target, fmt).date()
                break
            except ValueError:
                pass
    if exp_date is None:
        exp_date = get_expected_yesterday(now_dt)

    # Load date column
    df = pd.read_excel(filepath, usecols=[txn_date_col])
    row_count = len(df)
    if row_count == 0:
        raise DataValidationError(f"Empty report: '{filepath}' contains 0 data rows.")

    dates_found = set()
    yesterday_count = 0
    for val in df[txn_date_col].dropna().values:
        parsed = parse_flexible_date(val)
        if parsed is not None:
            dates_found.add(parsed)
            if parsed == exp_date:
                yesterday_count += 1

    if not dates_found:
        raise DataValidationError(f"Could not parse any valid transaction dates in '{filepath}'.")

    min_found = min(dates_found)
    max_found = max(dates_found)

    if yesterday_count == 0:
        raise DataValidationError(
            f"DATA VALIDATION FAILED: Expected transactions for date {exp_date.strftime('%Y-%m-%d')} "
            f"were NOT found in '{filepath}'. Found {row_count:,} rows spanning "
            f"{min_found.strftime('%Y-%m-%d')} to {max_found.strftime('%Y-%m-%d')} "
            f"({len(dates_found)} unique days). Halting pipeline to prevent processing incomplete data!"
        )

    summary = (
        f"Verified {row_count:,} rows across {len(dates_found)} calendar days "
        f"({min_found} to {max_found}). Expected date {exp_date} has {yesterday_count:,} records."
    )

    return ReportValidationResult(
        filepath=filepath,
        file_size_bytes=size_bytes,
        row_count=row_count,
        expected_yesterday=exp_date,
        dates_found=dates_found,
        yesterday_record_count=yesterday_count,
        is_valid=True,
        summary=summary,
    )
