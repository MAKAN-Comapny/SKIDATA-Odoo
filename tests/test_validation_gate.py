"""
Unit tests for Production-Grade Validation Gate (validator.py)
Tests:
  - Gate 1: Filename Date Freshness (Today, Yesterday, Stale, Target Date, Override)
  - Gate 2: Content Validation (Yesterday date presence, missing dates, corrupted file, schema)
"""
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from validator import (
    AST_TIMEZONE,
    DataValidationError,
    ReportValidationResult,
    StaleFileError,
    get_ast_now,
    get_expected_today,
    get_expected_yesterday,
    parse_date_from_filename,
    validate_filename_freshness,
    validate_parking_report_content,
)


class TestValidationGate(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        # Reference datetime for tests: 2026-11-23 10:00 AST
        self.ref_now = datetime(2026, 11, 23, 10, 0, 0, tzinfo=AST_TIMEZONE)
        self.ref_today = date(2026, 11, 23)
        self.ref_yesterday = date(2026, 11, 22)

        # Clear relevant env vars
        for v in ("TARGET_DATE", "ALLOW_STALE_FILE", "SKIP_CONTENT_VALIDATION"):
            if v in os.environ:
                del os.environ[v]

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        for v in ("TARGET_DATE", "ALLOW_STALE_FILE", "SKIP_CONTENT_VALIDATION"):
            if v in os.environ:
                del os.environ[v]

    # -------------------------------------------------------------
    # GATE 1: FILENAME DATE FRESHNESS TESTS
    # -------------------------------------------------------------
    def test_gate1_today_file_is_valid(self):
        filename = "23-Nov-26-Morning-Parking-Transactions.xlsx"
        res = validate_filename_freshness(filename, now_dt=self.ref_now)
        self.assertEqual(res, self.ref_today)

    def test_gate1_yesterday_file_is_valid(self):
        filename = "22-Nov-26-Evening-Parking-Transactions.xlsx"
        res = validate_filename_freshness(filename, now_dt=self.ref_now)
        self.assertEqual(res, self.ref_yesterday)

    def test_gate1_stale_file_rejected(self):
        # 3 days stale (20-Nov when today is 23-Nov)
        filename = "20-Nov-26-Evening-Parking-Transactions.xlsx"
        with self.assertRaises(StaleFileError) as ctx:
            validate_filename_freshness(filename, now_dt=self.ref_now)
        self.assertIn("STALE FILE DETECTED", str(ctx.exception))
        self.assertIn("3 day(s) old", str(ctx.exception))

    def test_gate1_target_date_match(self):
        filename = "15-Nov-26-Parking-Transactions.xlsx"
        res = validate_filename_freshness(filename, target_date="2026-11-15", now_dt=self.ref_now)
        self.assertEqual(res, date(2026, 11, 15))

    def test_gate1_target_date_mismatch(self):
        filename = "15-Nov-26-Parking-Transactions.xlsx"
        with self.assertRaises(StaleFileError) as ctx:
            validate_filename_freshness(filename, target_date="2026-11-16", now_dt=self.ref_now)
        self.assertIn("File date mismatch", str(ctx.exception))

    def test_gate1_allow_stale_override(self):
        filename = "10-Nov-26-Parking-Transactions.xlsx"
        # Overridden via parameter or env
        res = validate_filename_freshness(filename, allow_stale=True, now_dt=self.ref_now)
        self.assertEqual(res, date(2026, 11, 10))

        os.environ["ALLOW_STALE_FILE"] = "true"
        res_env = validate_filename_freshness(filename, now_dt=self.ref_now)
        self.assertEqual(res_env, date(2026, 11, 10))

    # -------------------------------------------------------------
    # GATE 2: CONTENT & YESTERDAY DATA VALIDATION TESTS
    # -------------------------------------------------------------
    def _create_mock_excel(self, rows: list[dict], path: str):
        df = pd.DataFrame(rows)
        # Pad with dummy columns to exceed 5KB
        for i in range(15):
            df[f"Extra_Col_{i}"] = f"Padding data value {i} to ensure standard Excel size"
        df.to_excel(path, index=False)

    def test_gate2_valid_report_with_yesterday_data(self):
        path = os.path.join(self.test_dir, "test_valid.xlsx")
        # 15 days of data ending on yesterday (2026-11-22)
        rows = []
        for d in range(15):
            txn_d = self.ref_yesterday - timedelta(days=d)
            for i in range(10):
                rows.append({
                    "Serial Number": f"SER_{d}_{i}",
                    "Transaction Date/Time": f"{txn_d.strftime('%Y-%m-%d')} 14:30:00",
                    "Payment": 15.0,
                    "Car Park": "View Mall",
                })
        self._create_mock_excel(rows, path)

        res = validate_parking_report_content(path, now_dt=self.ref_now)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.expected_yesterday, self.ref_yesterday)
        self.assertEqual(res.yesterday_record_count, 10)
        self.assertEqual(len(res.dates_found), 15)

    def test_gate2_fails_when_yesterday_data_missing(self):
        path = os.path.join(self.test_dir, "test_missing_yesterday.xlsx")
        # Data only up to 2 days ago (missing yesterday 2026-11-22)
        rows = []
        for d in range(1, 16):  # Starts from yesterday - 1 day
            txn_d = self.ref_yesterday - timedelta(days=d)
            for i in range(5):
                rows.append({
                    "Serial Number": f"SER_{d}_{i}",
                    "Transaction Date/Time": f"{txn_d.strftime('%Y-%m-%d')} 14:30:00",
                    "Payment": 15.0,
                    "Car Park": "View Mall",
                })
        self._create_mock_excel(rows, path)

        with self.assertRaises(DataValidationError) as ctx:
            validate_parking_report_content(path, now_dt=self.ref_now)
        self.assertIn("DATA VALIDATION FAILED", str(ctx.exception))
        self.assertIn("Expected transactions for date 2026-11-22 were NOT found", str(ctx.exception))

    def test_gate2_fails_on_corrupted_or_tiny_file(self):
        path = os.path.join(self.test_dir, "tiny.xlsx")
        with open(path, "wb") as f:
            f.write(b"not a real excel file")

        with self.assertRaises(DataValidationError) as ctx:
            validate_parking_report_content(path, now_dt=self.ref_now)
        self.assertIn("Corrupted or empty file", str(ctx.exception))

    def test_gate2_fails_on_missing_column(self):
        path = os.path.join(self.test_dir, "no_col.xlsx")
        rows = [{"Serial Number": "123", "Wrong Date Col": "2026-11-22", "Payment": 10.0}]
        self._create_mock_excel(rows, path)

        with self.assertRaises(DataValidationError) as ctx:
            validate_parking_report_content(path, now_dt=self.ref_now)
        self.assertIn("Missing required 'Transaction Date/Time' column", str(ctx.exception))

    def test_gate2_skip_content_validation_override(self):
        path = os.path.join(self.test_dir, "skip_test.xlsx")
        # Even if missing yesterday data, skip flag bypasses check
        rows = [{"Serial Number": "123", "Transaction Date/Time": "2026-10-01", "Payment": 10.0}]
        self._create_mock_excel(rows, path)

        os.environ["SKIP_CONTENT_VALIDATION"] = "true"
        res = validate_parking_report_content(path, now_dt=self.ref_now)
        self.assertTrue(res.is_valid)


if __name__ == "__main__":
    unittest.main()
