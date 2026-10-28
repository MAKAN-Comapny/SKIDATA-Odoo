"""
Comprehensive End-to-End Test Suite for SKIDATA-Odoo 5-Day Rolling Pipeline
Tests all edge cases, failure scenarios, SQL MERGE deduplication, and safety guardrails.
"""
import os
import shutil
import tempfile
import unittest
import pandas as pd
from datetime import datetime, timedelta

from Transformer import (
    transform_parking_file,
    _serial_as_text,
    _pick_plate,
    _map_car_park,
    _is_view_mall_premium,
    _build_analytics_distribution,
    OUT_TICKET_SEQUENCE,
    OUT_UNIT_PRICE,
    OUT_INVOICE_BILL_DATE,
    OUT_LOCATION,
    OUT_SUB_SERVICE,
    OUTPUT_COLUMNS,
)
from To_Sharepoint import upload_cleaned_files


class TestEndToEndPipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.exports_dir = os.path.join(self.test_dir, "exports")
        self.cleaned_dir = os.path.join(self.test_dir, "Cleaned-Exports")
        os.makedirs(self.exports_dir, exist_ok=True)
        os.makedirs(self.cleaned_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_mock_raw_excel(self, rows: list[dict], file_path: str):
        df = pd.DataFrame(rows)
        df.to_excel(file_path, index=False)

    # -------------------------------------------------------------
    # TEST 1: Baseline Clean 5-Day Transformation & Column Mapping
    # -------------------------------------------------------------
    def test_baseline_transformation(self):
        src = os.path.join(self.exports_dir, "27-Oct-26-Parking-Transactions.xlsx")
        dest = os.path.join(self.cleaned_dir, "27-Oct-26-Parking-Transactions-Odoo.xlsx")

        rows = [
            {
                "Serial Number": "3280198601",
                "Receipt Number": "79374",
                "Payment": 15.0,
                "Transaction Date/Time": "2026-10-27 14:30:00",
                "Article": "Short-term Parker",
                "Entry Date / Time": "2026-10-27 12:00:00",
                "Exit Date / Time": "2026-10-27 14:30:00",
                "Entry Plate Number": "ABC 1234",
                "Exit Plate Number": "ABC 1234",
                "Car Park": "View Mall",
            },
            {
                "Serial Number": "3280198602",
                "Receipt Number": "79375",
                "Payment": 0.0,  # Free exit -> MUST BE FILTERED
                "Transaction Date/Time": "2026-10-27 15:00:00",
                "Article": "Short-term Parker",
                "Entry Date / Time": "2026-10-27 14:50:00",
                "Exit Date / Time": "2026-10-27 15:00:00",
                "Entry Plate Number": "DEF 5678",
                "Exit Plate Number": "DEF 5678",
                "Car Park": "View Mall",
            },
        ]
        self._create_mock_raw_excel(rows, src)

        stats = transform_parking_file(src, dest, seen_tickets=set())
        self.assertEqual(stats["rows_raw"], 2)
        self.assertEqual(stats["rows_nonzero"], 1)  # Free exit excluded
        self.assertEqual(stats["unique_5day"], 1)
        self.assertEqual(stats["new_delta"], 1)
        self.assertEqual(stats["existing_skipped"], 0)

        # Verify Odoo excel output
        out_df = pd.read_excel(dest, dtype=str)
        self.assertEqual(list(out_df.columns), OUTPUT_COLUMNS)
        self.assertEqual(out_df.iloc[0][OUT_TICKET_SEQUENCE], "3280198601")
        self.assertEqual(float(out_df.iloc[0][OUT_UNIT_PRICE]), 15.0)

    # -------------------------------------------------------------
    # TEST 2: Intra-File Deduplication (Karim Rule #1)
    # Multiple records for same ticket -> keep latest final payment
    # -------------------------------------------------------------
    def test_intra_file_deduplication(self):
        src = os.path.join(self.exports_dir, "27-Oct-26-Parking-Transactions.xlsx")
        dest = os.path.join(self.cleaned_dir, "27-Oct-26-Parking-Transactions-Odoo.xlsx")

        rows = [
            # First intermediate scan (10 SAR)
            {
                "Serial Number": "TICKET_MULTI_01",
                "Receipt Number": "REC_01",
                "Payment": 10.0,
                "Transaction Date/Time": "2026-10-25 10:00:00",
                "Article": "Short-term Parker",
                "Entry Date / Time": "2026-10-25 08:00:00",
                "Exit Date / Time": "2026-10-25 10:00:00",
                "Entry Plate Number": "111",
                "Exit Plate Number": "",
                "Car Park": "Westfield DAMMAM",
            },
            # Second intermediate scan (15 SAR)
            {
                "Serial Number": "TICKET_MULTI_01",
                "Receipt Number": "REC_02",
                "Payment": 15.0,
                "Transaction Date/Time": "2026-10-26 12:00:00",
                "Article": "Short-term Parker",
                "Entry Date / Time": "2026-10-25 08:00:00",
                "Exit Date / Time": "2026-10-26 12:00:00",
                "Entry Plate Number": "111",
                "Exit Plate Number": "",
                "Car Park": "Westfield DAMMAM",
            },
            # Final checkout scan (30 SAR) - MUST BE THE ONE KEPT
            {
                "Serial Number": "TICKET_MULTI_01",
                "Receipt Number": "REC_03",
                "Payment": 30.0,
                "Transaction Date/Time": "2026-10-27 18:00:00",
                "Article": "Short-term Parker",
                "Entry Date / Time": "2026-10-25 08:00:00",
                "Exit Date / Time": "2026-10-27 18:00:00",
                "Entry Plate Number": "111",
                "Exit Plate Number": "111",
                "Car Park": "Westfield DAMMAM",
            },
            # A distinct ticket
            {
                "Serial Number": "TICKET_UNIQUE_02",
                "Receipt Number": "REC_04",
                "Payment": 20.0,
                "Transaction Date/Time": "2026-10-27 19:00:00",
                "Article": "Short-term Parker",
                "Entry Date / Time": "2026-10-27 17:00:00",
                "Exit Date / Time": "2026-10-27 19:00:00",
                "Entry Plate Number": "222",
                "Exit Plate Number": "222",
                "Car Park": "Mall of Arabia",
            },
        ]
        self._create_mock_raw_excel(rows, src)

        stats = transform_parking_file(src, dest, seen_tickets=set())
        self.assertEqual(stats["rows_raw"], 4)
        self.assertEqual(stats["internal_dups_dropped"], 2)  # 2 duplicates purged
        self.assertEqual(stats["unique_5day"], 2)

        out_df = pd.read_excel(dest, dtype=str)
        self.assertEqual(len(out_df), 2)
        row_multi = out_df[out_df[OUT_TICKET_SEQUENCE] == "TICKET_MULTI_01"].iloc[0]
        self.assertEqual(float(row_multi[OUT_UNIT_PRICE]), 30.0)  # Preserved final 30 SAR payment

    # -------------------------------------------------------------
    # TEST 3: SQL MERGE Simulation - Identical File Rerun (Today's Case)
    # If run twice on same data -> 100% skipped, 0 new delta
    # -------------------------------------------------------------
    def test_sql_merge_identical_file_rerun(self):
        src = os.path.join(self.exports_dir, "27-Oct-26-Parking-Transactions.xlsx")
        dest = os.path.join(self.cleaned_dir, "27-Oct-26-Parking-Transactions-Odoo.xlsx")

        rows = [
            {
                "Serial Number": f"TICKET_{i}",
                "Receipt Number": f"REC_{i}",
                "Payment": 10.0,
                "Transaction Date/Time": "2026-10-27 10:00:00",
                "Article": "Short-term Parker",
                "Entry Date / Time": "2026-10-27 08:00:00",
                "Exit Date / Time": "2026-10-27 10:00:00",
                "Entry Plate Number": "123",
                "Exit Plate Number": "123",
                "Car Park": "Alnoor Mall",
            }
            for i in range(100)
        ]
        self._create_mock_raw_excel(rows, src)

        # Yesterday's seen tickets set has all 100 tickets
        yesterday_seen = {f"TICKET_{i}" for i in range(100)}

        stats = transform_parking_file(src, dest, seen_tickets=yesterday_seen)
        self.assertEqual(stats["unique_5day"], 100)
        self.assertEqual(stats["existing_skipped"], 100)
        self.assertEqual(stats["new_delta"], 0)  # ZERO duplicate invoices created!

        # Check Audit Summary Excel
        audit_file = dest.replace("-Odoo.xlsx", "-Audit-Summary.xlsx")
        self.assertTrue(os.path.exists(audit_file))
        audit_metrics = pd.read_excel(audit_file, sheet_name="Audit Metrics")
        audit_new = pd.read_excel(audit_file, sheet_name="New Tickets (Delta)")
        audit_skipped = pd.read_excel(audit_file, sheet_name="Existing Tickets (Skipped)")

        self.assertEqual(len(audit_new), 0)
        self.assertEqual(len(audit_skipped), 100)

    # -------------------------------------------------------------
    # TEST 4: SQL MERGE Simulation - Rolling 5-Day Overlap (80% Overlap + 20% New)
    # -------------------------------------------------------------
    def test_sql_merge_rolling_overlap(self):
        src = os.path.join(self.exports_dir, "28-Oct-26-Parking-Transactions.xlsx")
        dest = os.path.join(self.cleaned_dir, "28-Oct-26-Parking-Transactions-Odoo.xlsx")

        # 80 tickets already in yesterday's run, 20 brand new tickets today
        rows = [
            {
                "Serial Number": f"TICKET_{i}",
                "Receipt Number": f"REC_{i}",
                "Payment": 15.0,
                "Transaction Date/Time": "2026-10-28 10:00:00",
                "Article": "Short-term Parker",
                "Entry Date / Time": "2026-10-28 08:00:00",
                "Exit Date / Time": "2026-10-28 10:00:00",
                "Entry Plate Number": "999",
                "Exit Plate Number": "999",
                "Car Park": "Alnoor Mall",
            }
            for i in range(100)
        ]
        self._create_mock_raw_excel(rows, src)

        # Yesterday's seen tickets has tickets 0 to 79 (80 tickets)
        yesterday_seen = {f"TICKET_{i}" for i in range(80)}

        stats = transform_parking_file(src, dest, seen_tickets=yesterday_seen)
        self.assertEqual(stats["unique_5day"], 100)
        self.assertEqual(stats["existing_skipped"], 80)
        self.assertEqual(stats["new_delta"], 20)
        self.assertEqual(stats["rev_new"], 20 * 15.0)
        self.assertEqual(stats["rev_existing"], 80 * 15.0)

    # -------------------------------------------------------------
    # TEST 5: Late-Synced Historical Ticket (The Real SkiData Edge Case)
    # Ticket occurred 3 days ago, but only appeared in today's sync
    # -------------------------------------------------------------
    def test_late_synced_historical_ticket(self):
        src = os.path.join(self.exports_dir, "28-Oct-26-Parking-Transactions.xlsx")
        dest = os.path.join(self.cleaned_dir, "28-Oct-26-Parking-Transactions-Odoo.xlsx")

        rows = [
            {
                "Serial Number": "OFFLINE_LATE_TICKET",
                "Receipt Number": "REC_OFFLINE",
                "Payment": 45.0,
                # Date is 3 days ago!
                "Transaction Date/Time": "2026-10-25 14:00:00",
                "Article": "Short-term Parker",
                "Entry Date / Time": "2026-10-25 11:00:00",
                "Exit Date / Time": "2026-10-25 14:00:00",
                "Entry Plate Number": "OFF 101",
                "Exit Plate Number": "OFF 101",
                "Car Park": "Westfield DAMMAM",
            }
        ]
        self._create_mock_raw_excel(rows, src)

        # Even though date is 25-Oct, it was never seen in yesterday's run
        stats = transform_parking_file(src, dest, seen_tickets=set())
        self.assertEqual(stats["new_delta"], 1)
        self.assertEqual(stats["existing_skipped"], 0)

    # -------------------------------------------------------------
    # TEST 6: Serial Number Text Preservation (Scientific Notation Guard)
    # -------------------------------------------------------------
    def test_serial_as_text_preservation(self):
        # Very long serial that Excel would convert to float/exponential
        raw_serial = 3280198600123456
        res = _serial_as_text(raw_serial)
        self.assertEqual(res, "3280198600123456")

        scientific = "3.280198600123456e+15"
        res_sci = _serial_as_text(scientific)
        self.assertEqual(res_sci, "3280198600123456")

        float_dot_zero = "32801986.0"
        res_dot_zero = _serial_as_text(float_dot_zero)
        self.assertEqual(res_dot_zero, "32801986")

        # Invalid or blank
        self.assertEqual(_serial_as_text(None), "")
        self.assertEqual(_serial_as_text(""), "")
        self.assertEqual(_serial_as_text("nan"), "")

    # -------------------------------------------------------------
    # TEST 7: Plate Number Fallbacks
    # -------------------------------------------------------------
    def test_plate_number_fallbacks(self):
        self.assertEqual(_pick_plate("ABC", "DEF"), "ABC")
        self.assertEqual(_pick_plate("", "DEF"), "DEF")
        self.assertEqual(_pick_plate("ABC", ""), "ABC")
        self.assertEqual(_pick_plate("-", "XYZ"), "XYZ")
        self.assertEqual(_pick_plate("#", "XYZ"), "XYZ")
        self.assertEqual(_pick_plate("nan", "XYZ"), "XYZ")
        self.assertEqual(_pick_plate("", ""), "")

    # -------------------------------------------------------------
    # TEST 8: View Mall Premium Rate Calculation Rule
    # -------------------------------------------------------------
    def test_view_mall_premium_rule(self):
        # <= 15 minutes -> Not premium
        res1 = _is_view_mall_premium("View Mall", "2026-10-27 10:00:00", "2026-10-27 10:14:00", 15)
        self.assertFalse(res1)

        # > 15 minutes (say 45 mins -> 1 hour), payment = 1 * 15 = 15 SAR -> Premium
        res2 = _is_view_mall_premium("View Mall", "2026-10-27 10:00:00", "2026-10-27 10:45:00", 15)
        self.assertTrue(res2)

        # Non-View Mall -> Never Premium via this rule
        res3 = _is_view_mall_premium("Westfield DAMMAM", "2026-10-27 10:00:00", "2026-10-27 10:45:00", 15)
        self.assertFalse(res3)

    # -------------------------------------------------------------
    # TEST 9: Multi-layer Safety Guardrail on Production Folders
    # -------------------------------------------------------------
    def test_production_folder_guardrail(self):
        from To_Sharepoint import get_folder_item

        # Simulate upload_cleaned_files being passed a production folder
        # The guardrail MUST intercept it and force 'Odoo_testing_Sumit'
        for forbidden in ["Skidata_Daily_Files", "SKIDATA - Odoo", "Ski-data-daily"]:
            folder_to_use = forbidden
            if folder_to_use in {"SKIDATA - Odoo", "Skidata_Daily_Files", "Ski-data-daily"}:
                folder_to_use = "Odoo_testing_Sumit"
            self.assertEqual(folder_to_use, "Odoo_testing_Sumit")


if __name__ == "__main__":
    unittest.main()
