"""
SKIDATA-Odoo Pipeline

Orchestrates parking-transactions-only workflow:
  1. SharePoint download - Latest raw Parking Transactions from Skidata_Daily_Files
  2. Transformer - Filter Payment != 0 and remap columns for Odoo
  3. To_Sharepoint - Upload transformed *-Odoo.xlsx to folder "SKIDATA - Odoo"

The portal scraper is no longer used: daily raw reports are already uploaded
to SharePoint by another job.

Set UPLOAD_TO_SHAREPOINT=false in .env to skip step 3.
"""
import os
import sys

from dotenv import load_dotenv

from sharepoint_parking_download import fetch_latest_parking_to_exports
from Transformer import run as transform_exports


def main():
    """Run SharePoint download -> transform -> upload to SharePoint."""
    load_dotenv()

    print("=" * 60)
    print("Step 1/3: Download Parking Transactions from SharePoint...")
    print("=" * 60)
    fetch_latest_parking_to_exports()

    print("\n" + "=" * 60)
    print("Step 2/3: Transforming for Odoo (Payment != 0 + column map)...")
    print("=" * 60)
    transform_exports()

    upload = os.getenv("UPLOAD_TO_SHAREPOINT", "true").lower() in ("true", "1", "yes")
    if upload:
        from To_Sharepoint import main as upload_to_sharepoint

        print("\n" + "=" * 60)
        print("Step 3/3: Uploading transformed file to SharePoint (SKIDATA - Odoo)...")
        print("=" * 60)
        upload_to_sharepoint()
    else:
        print("\nSharePoint upload skipped (UPLOAD_TO_SHAREPOINT=false).")

    print("\n" + "=" * 60)
    print("Pipeline complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
    sys.exit(0)
