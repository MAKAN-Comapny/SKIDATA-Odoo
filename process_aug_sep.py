"""
Process August and September 2026 Parking Transactions for Odoo
--------------------------------------------------------------
Workflow:
  1. Download '*-Parking-Transactions*.xlsx' from SharePoint folder:
     'Validated_Docs/old/Aug' into 'exports/'
  2. Run Transformer.py (filters Payment != 0, applies View Mall 15 SAR rate, maps columns & analytic accounts)
  3. Upload generated '*-Parking-Transactions-Odoo.xlsx' to SharePoint folder:
     'Odoo_aug_sep'
  4. Repeat for September:
     Download from 'Validated_Docs/old/Sep' -> Transform -> Upload to 'Odoo_aug_sep'
"""
import glob
import os
import shutil
import sys
from dotenv import load_dotenv

load_dotenv()

from sharepoint_parking_download import (
    get_access_token,
    get_site_id,
    list_folder_files,
    download_file,
    encode_drive_path,
)
from Transformer import run as transform_exports
from To_Sharepoint import ensure_folder_path, upload_file

EXPORTS_DIR = "exports"
CLEANED_EXPORTS_DIR = "Cleaned-Exports"
TARGET_ODOO_FOLDER = os.getenv("TARGET_FOLDER_PATH_ODOO") or "Odoo_aug_sep"

MONTH_CONFIGS = [
    {
        "month_name": "August 2026",
        "source_folder": "Validated_Docs/old/Aug",
        "dest_folder": TARGET_ODOO_FOLDER,
    },
    {
        "month_name": "September 2026",
        "source_folder": "Validated_Docs/old/Sep",
        "dest_folder": TARGET_ODOO_FOLDER,
    },
]


def clear_directory(path: str):
    """Remove all files inside a directory to prevent cross-contamination."""
    if os.path.exists(path):
        for f in os.listdir(path):
            fp = os.path.join(path, f)
            if os.path.isfile(fp):
                os.remove(fp)
    os.makedirs(path, exist_ok=True)


def find_parking_file_item(items: list, folder_path: str) -> dict:
    """Find the Parking Transactions file in the SharePoint folder."""
    candidates = []
    for item in items:
        if not item.get("file"):
            continue
        name = item.get("name", "")
        if not name.lower().endswith((".xlsx", ".xlsm")):
            continue
        if "parking-transactions" in name.lower() and "odoo" not in name.lower():
            candidates.append(item)

    if not candidates:
        all_names = [i.get("name", "") for i in items if i.get("file")]
        raise RuntimeError(
            f"No 'Parking-Transactions' .xlsx file found in SharePoint folder '{folder_path}'.\n"
            f"Files found: {all_names or '(empty)'}"
        )
    # Pick the most recently modified or the first matching
    candidates.sort(key=lambda x: x.get("lastModifiedDateTime", ""), reverse=True)
    return candidates[0]


def process_month(config: dict, site_id: str, headers: dict):
    month = config["month_name"]
    source_folder = config["source_folder"]
    dest_folder = config["dest_folder"]

    print("\n" + "=" * 65)
    print(f"PROCESSING: {month}")
    print(f"Source Folder:      SharePoint -> {source_folder}")
    print(f"Destination Folder: SharePoint -> {dest_folder}")
    print("=" * 65)

    clear_directory(EXPORTS_DIR)
    clear_directory(CLEANED_EXPORTS_DIR)

    # 1. List files in SharePoint source folder
    print(f"Reading folder '{source_folder}'...")
    items = list_folder_files(headers, site_id, source_folder)
    parking_item = find_parking_file_item(items, source_folder)
    raw_name = parking_item["name"]
    local_raw_path = os.path.join(EXPORTS_DIR, raw_name)

    print(f"Downloading: {raw_name}...")
    download_file(parking_item, local_raw_path)
    print(f"Saved locally ({os.path.getsize(local_raw_path):,} bytes).")

    # 2. Transform for Odoo
    print("\nTransforming for Odoo (Payment != 0, View Mall rate, analytic distribution)...")
    transform_exports()

    # 3. Locate transformed file in Cleaned-Exports
    odoo_files = glob.glob(os.path.join(CLEANED_EXPORTS_DIR, "*Parking-Transactions-Odoo.xlsx"))
    if not odoo_files:
        odoo_files = glob.glob(os.path.join(CLEANED_EXPORTS_DIR, "*.xlsx"))
    if not odoo_files:
        raise RuntimeError(f"No transformed Odoo file generated for {month} in {CLEANED_EXPORTS_DIR}/")

    odoo_file_path = odoo_files[0]
    odoo_file_name = os.path.basename(odoo_file_path)
    print(f"Transformed output: {odoo_file_name} ({os.path.getsize(odoo_file_path):,} bytes)")

    # 4. Upload to SharePoint destination folder
    print(f"\nEnsuring destination folder exists: '{dest_folder}'...")
    ensure_folder_path(site_id, headers, dest_folder)

    print(f"Uploading {odoo_file_name} to SharePoint -> '{dest_folder}'...")
    res = upload_file(site_id, headers, odoo_file_path, folder_path=dest_folder)
    print(f"SUCCESS: Uploaded {odoo_file_name} -> {dest_folder}/ (WebUrl: {res.get('webUrl', 'Done')})")


def main():
    print("=" * 65)
    print("SKIDATA-Odoo: Historical August & September Pipeline")
    print("=" * 65)

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    site_id = get_site_id(headers)

    for cfg in MONTH_CONFIGS:
        try:
            process_month(cfg, site_id, headers)
        except Exception as e:
            print(f"ERROR processing {cfg['month_name']}: {e}")
            raise

    print("\n" + "=" * 65)
    print(f"ALL HISTORICAL ODOO FILES SUCCESSFULLY GENERATED AND UPLOADED TO '{TARGET_ODOO_FOLDER}'!")
    print("=" * 65)


if __name__ == "__main__":
    main()
