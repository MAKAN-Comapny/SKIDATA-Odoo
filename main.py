"""
SKIDATA-Odoo Pipeline

Orchestrates parking-transactions-only workflow:
  1. Scraper  - Login to SKIDATA portal, download Parking Transactions to exports/
  2. Transformer - Filter Payment != 0 and remap columns for Odoo
  3. To_Sharepoint - Upload transformed file to SharePoint folder "SKIDATA - Odoo"

Set UPLOAD_TO_SHAREPOINT=false in .env to skip step 3.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

from scraper import login_and_open_portal
from Transformer import run as transform_exports


def main():
    """Run scrape -> transform -> upload to SharePoint."""
    load_dotenv()

    print("=" * 60)
    print("Step 1/3: Scraping Parking Transactions report...")
    print("=" * 60)
    asyncio.run(login_and_open_portal())

    print("\n" + "=" * 60)
    print("Step 2/3: Transforming for Odoo (Payment != 0 + column map)...")
    print("=" * 60)
    transform_exports()

    upload = os.getenv("UPLOAD_TO_SHAREPOINT", "true").lower() in ("true", "1", "yes")
    if upload:
        from To_Sharepoint import main as upload_to_sharepoint

        print("\n" + "=" * 60)
        print("Step 3/3: Uploading to SharePoint (SKIDATA - Odoo)...")
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
