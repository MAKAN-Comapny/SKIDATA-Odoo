"""
SKIDATA-Odoo Pipeline (5-Day Rolling Sandbox / Testing Mode)

Workflow:
  1. SharePoint download:
     - Connects to SharePoint folder 'Ski Data 5'.
     - Downloads latest rolling 5-day raw Parking Transactions.
     - Inspects destination folder 'Odoo_testing_Sumit' to read previously seen
       Ticket Sequences for SQL MERGE delta tracking.
  2. Transformer:
     - Filters non-zero payments (Payment != 0).
     - Standardizes columns and maps locations, analytics distribution, and View Mall rules.
     - Intra-file deduplication (Ticket Sequence) to ensure 0 duplicates.
     - Cross-run SQL MERGE delta simulation (identifies Brand New tickets vs Existing).
     - Generates both clean <name>-Odoo.xlsx and <name>-Audit-Summary.xlsx.
  3. SharePoint upload:
     - Uploads both transformed Odoo file and Audit-Summary to 'Odoo_testing_Sumit'.
     - ZERO calls to live Odoo ERP XML-RPC (100% safe sandbox testing).
"""
import os
import sys

from dotenv import load_dotenv

from sharepoint_parking_download import fetch_latest_parking_and_seen_tickets
from Transformer import run as transform_exports
from To_Sharepoint import upload_cleaned_files


def main():
    """Run SharePoint download -> transform with 5-day deduplication & audit -> upload to SharePoint."""
    load_dotenv()

    source = (
        os.getenv("SHAREPOINT_SOURCE_FOLDER")
        or os.getenv("TARGET_FOLDER_PATH")
        or "Ski Data 5"
    )
    dest = os.getenv("TARGET_FOLDER_PATH_ODOO") or "Odoo_testing_Sumit"

    print("=" * 70)
    print("SKIDATA-Odoo 5-Day Rolling Pipeline (Testing Mode)")
    print(f"Source:             SharePoint -> {source}")
    print(f"Destination:        SharePoint -> {dest}")
    print(f"Live ERP API:       OFF (Safe Sandbox Testing - Zero Risk)")
    print("=" * 70)

    print("\nStep 1/3: Downloading latest 5-Day Parking Transactions from SharePoint...")
    raw_path, seen_tickets = fetch_latest_parking_and_seen_tickets(
        source_folder=source,
        dest_folder=dest,
    )

    print("\n" + "=" * 70)
    print("Step 2/3: Transforming for Odoo & Generating SQL MERGE Audit...")
    print("=" * 70)
    transform_exports(seen_tickets=seen_tickets)

    upload = os.getenv("UPLOAD_TO_SHAREPOINT", "true").lower() in ("true", "1", "yes")
    if upload:
        print("\n" + "=" * 70)
        print(f"Step 3/3: Uploading transformed files to SharePoint ({dest})...")
        print("=" * 70)
        upload_cleaned_files(dest_folder=dest)
    else:
        print("\nSharePoint upload skipped (UPLOAD_TO_SHAREPOINT=false).")

    print("\n" + "=" * 70)
    print("Pipeline completed successfully.")
    print("=" * 70)


if __name__ == "__main__":
    main()
    sys.exit(0)
