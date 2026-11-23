"""
Unit tests for file naming conventions, upload time ordering, and strict audit exclusion.
Ensures:
1. Audit and Summary files are NEVER picked up by import or raw downloaders.
2. Upload timestamps order Morning and Evening runs correctly (Evening is prioritized if both exist).
3. Optional RUN_TAG filters for specific runs (Morning, Evening, 12Noon).
4. Multiple TARGET_DATE formats are parsed safely without errors.
"""
import os
import sys
import unittest
from datetime import datetime

# Add root and skidata-odoo-share to sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
share_dir = os.path.join(root_dir, "skidata-odoo-share")
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)
if share_dir not in sys.path:
    sys.path.insert(0, share_dir)

import sharepoint_download
import sharepoint_parking_download


class TestFileNamingAndAuditExclusion(unittest.TestCase):
    def setUp(self):
        # Clear env variables before each test
        for var in ("TARGET_DATE", "RUN_TAG"):
            if var in os.environ:
                del os.environ[var]

    def tearDown(self):
        for var in ("TARGET_DATE", "RUN_TAG"):
            if var in os.environ:
                del os.environ[var]

    # -------------------------------------------------------------------------
    # 1. Audit File Exclusion: Audit files uploaded LATER must NEVER be picked
    # -------------------------------------------------------------------------
    def test_odoo_export_strictly_excludes_audit_files(self):
        """
        In SharePoint, the Audit file is uploaded ~5 seconds AFTER the Odoo file.
        Its lastModifiedDateTime is therefore GREATER than the Odoo file.
        pick_latest_export MUST ignore the Audit file and select the Odoo file.
        """
        items = [
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions-Odoo.xlsx",
                "lastModifiedDateTime": "2026-11-23T08:30:10Z",
            },
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions-Audit-Summary.xlsx",
                "lastModifiedDateTime": "2026-11-23T08:30:15Z",  # Newer timestamp!
            },
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions-Odoo-Audit.xlsx",
                "lastModifiedDateTime": "2026-11-23T08:30:20Z",  # Has 'Odoo' and 'Audit' + newer!
            },
        ]

        selected = sharepoint_download.pick_latest_export(items)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["name"], "23-Nov-26-Morning-Parking-Transactions-Odoo.xlsx")

    # -------------------------------------------------------------------------
    # 2. Morning vs Evening: Upload Time Ordering
    # -------------------------------------------------------------------------
    def test_evening_file_prioritized_over_morning_by_upload_time(self):
        """
        When both Morning and Evening files are present, the Evening file
        was uploaded later (higher lastModifiedDateTime) and must be picked.
        """
        items = [
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions-Odoo.xlsx",
                "lastModifiedDateTime": "2026-11-23T08:30:00Z",
            },
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions-Audit-Summary.xlsx",
                "lastModifiedDateTime": "2026-11-23T08:30:05Z",
            },
            {
                "file": {},
                "name": "23-Nov-26-Evening-Parking-Transactions-Odoo.xlsx",
                "lastModifiedDateTime": "2026-11-23T17:30:00Z",  # Latest run!
            },
            {
                "file": {},
                "name": "23-Nov-26-Evening-Parking-Transactions-Audit-Summary.xlsx",
                "lastModifiedDateTime": "2026-11-23T17:30:05Z",  # Audit file
            },
        ]

        selected = sharepoint_download.pick_latest_export(items)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["name"], "23-Nov-26-Evening-Parking-Transactions-Odoo.xlsx")

    # -------------------------------------------------------------------------
    # 3. Explicit RUN_TAG Filtering
    # -------------------------------------------------------------------------
    def test_run_tag_filter_morning_and_evening(self):
        items = [
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions-Odoo.xlsx",
                "lastModifiedDateTime": "2026-11-23T08:30:00Z",
            },
            {
                "file": {},
                "name": "23-Nov-26-Evening-Parking-Transactions-Odoo.xlsx",
                "lastModifiedDateTime": "2026-11-23T17:30:00Z",
            },
        ]

        # Case A: Request Morning explicitly
        os.environ["RUN_TAG"] = "Morning"
        selected_m = sharepoint_download.pick_latest_export(items)
        self.assertEqual(selected_m["name"], "23-Nov-26-Morning-Parking-Transactions-Odoo.xlsx")

        # Case B: Request Evening explicitly
        os.environ["RUN_TAG"] = "Evening"
        selected_e = sharepoint_download.pick_latest_export(items)
        self.assertEqual(selected_e["name"], "23-Nov-26-Evening-Parking-Transactions-Odoo.xlsx")

    # -------------------------------------------------------------------------
    # 4. Raw Parking Transactions (sharepoint_parking_download) Exclusions
    # -------------------------------------------------------------------------
    def test_raw_parking_download_excludes_odoo_and_audits(self):
        items = [
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions.xlsx",  # Raw file
                "lastModifiedDateTime": "2026-11-23T08:00:00Z",
            },
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions-Odoo.xlsx",  # Transformed
                "lastModifiedDateTime": "2026-11-23T08:30:00Z",
            },
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions-Audit-Summary.xlsx",  # Audit
                "lastModifiedDateTime": "2026-11-23T08:30:05Z",
            },
        ]

        selected = sharepoint_parking_download.pick_latest_parking(items)
        self.assertEqual(selected["name"], "23-Nov-26-Morning-Parking-Transactions.xlsx")

    # -------------------------------------------------------------------------
    # 5. Robust TARGET_DATE Formats
    # -------------------------------------------------------------------------
    def test_target_date_prefix_parsing(self):
        # Format 1: ISO YYYY-MM-DD
        os.environ["TARGET_DATE"] = "2026-11-23"
        prefixes_iso = sharepoint_download.target_date_prefixes()
        self.assertIn("23-Nov-26-", prefixes_iso)

        # Format 2: DD-Mon-YY
        os.environ["TARGET_DATE"] = "23-Nov-26"
        prefixes_dd = sharepoint_download.target_date_prefixes()
        self.assertIn("23-Nov-26-", prefixes_dd)

        # Format 3: DD-Mon-YYYY
        os.environ["TARGET_DATE"] = "23-Nov-2026"
        prefixes_full = sharepoint_download.target_date_prefixes()
        self.assertIn("23-Nov-26-", prefixes_full)

    # -------------------------------------------------------------------------
    # 6. Target Date Combined with Morning/Evening
    # -------------------------------------------------------------------------
    def test_target_date_with_morning_and_evening(self):
        os.environ["TARGET_DATE"] = "2026-11-22"
        items = [
            {
                "file": {},
                "name": "21-Nov-26-Evening-Parking-Transactions-Odoo.xlsx",
                "lastModifiedDateTime": "2026-11-21T18:00:00Z",
            },
            {
                "file": {},
                "name": "22-Nov-26-Morning-Parking-Transactions-Odoo.xlsx",
                "lastModifiedDateTime": "2026-11-22T08:00:00Z",
            },
            {
                "file": {},
                "name": "22-Nov-26-Evening-Parking-Transactions-Odoo.xlsx",
                "lastModifiedDateTime": "2026-11-22T18:00:00Z",
            },
            {
                "file": {},
                "name": "22-Nov-26-Evening-Parking-Transactions-Audit-Summary.xlsx",
                "lastModifiedDateTime": "2026-11-22T18:05:00Z",
            },
            {
                "file": {},
                "name": "23-Nov-26-Morning-Parking-Transactions-Odoo.xlsx",
                "lastModifiedDateTime": "2026-11-23T08:00:00Z",
            },
        ]

        # Target date is 22-Nov, so it filters to 22-Nov only, ignores 23-Nov,
        # ignores the Audit file, and picks the Evening 22-Nov file!
        selected = sharepoint_download.pick_latest_export(items)
        self.assertEqual(selected["name"], "22-Nov-26-Evening-Parking-Transactions-Odoo.xlsx")


if __name__ == "__main__":
    unittest.main()
