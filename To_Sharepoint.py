"""
Upload Parking Transactions files from Cleaned-Exports/ to Microsoft SharePoint.
Run Transformer.py first to ensure Cleaned-Exports/ is populated.
"""
import os
import re
import glob
import requests
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
SITE_NAME = os.getenv("SHAREPOINT_SITE_NAME")
TARGET_FOLDER_PATH = (os.getenv("TARGET_FOLDER_PATH") or "SKIDATA - Odoo").strip()

CLEANED_EXPORTS_DIR = "Cleaned-Exports"
# Prefer transformed Odoo files; fall back to any Parking-Transactions workbook.
ODOO_FILE_MARKER = "Parking-Transactions-Odoo"
PARKING_FILE_MARKER = "Parking-Transactions"


def get_access_token():
    """Get Microsoft Graph access token using app credentials."""
    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": "https://graph.microsoft.com/.default",
    }
    r = requests.post(token_url, data=data)
    r.raise_for_status()
    return r.json()["access_token"]


def get_site_id(headers):
    """Find SharePoint site ID by URL or display name."""
    site_input = SITE_NAME.strip()

    url_match = re.match(r"https?://([^/]+)(/.*)?", site_input)
    if url_match:
        hostname = url_match.group(1)
        path = (url_match.group(2) or "/").rstrip("/") or "/"
        if not path.startswith("/"):
            path = "/" + path
        get_url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:{path}"
        r = requests.get(get_url, headers=headers)
        r.raise_for_status()
        return r.json()["id"]

    site_search_url = (
        f"https://graph.microsoft.com/v1.0/sites?search={requests.utils.quote(site_input)}"
    )
    r = requests.get(site_search_url, headers=headers)
    r.raise_for_status()
    sites = r.json().get("value", [])
    if not sites:
        raise ValueError(f"No site found with name or URL: {SITE_NAME}")
    return sites[0]["id"]


def ensure_folder_path(site_id, headers):
    """Create nested folders if they don't exist."""
    parts = [p for p in TARGET_FOLDER_PATH.split("/") if p.strip()]
    current = ""

    for part in parts:
        current = f"{current}/{part}" if current else part
        check = requests.get(
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{current}",
            headers=headers,
        )

        if check.status_code == 404:
            parent = current.rsplit("/", 1)[0] if "/" in current else ""
            create_url = (
                f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{parent}:/children"
                if parent
                else f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root/children"
            )
            folder_payload = {
                "name": part,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "replace",
            }
            requests.post(create_url, headers=headers, json=folder_payload)


def upload_file(site_id, headers, file_path):
    """Upload a single file to SharePoint."""
    file_name = os.path.basename(file_path)
    upload_url = (
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:"
        f"/{TARGET_FOLDER_PATH}/{file_name}:/content"
    )

    with open(file_path, "rb") as f:
        r = requests.put(upload_url, headers=headers, data=f)

    r.raise_for_status()
    return r.json()


def main():
    missing = [
        k
        for k, v in {
            "TENANT_ID": TENANT_ID,
            "CLIENT_ID": CLIENT_ID,
            "CLIENT_SECRET": CLIENT_SECRET,
            "SHAREPOINT_SITE_NAME": SITE_NAME,
        }.items()
        if not v or not str(v).strip()
    ]
    if missing:
        raise SystemExit(
            f"Missing required .env variables: {', '.join(missing)}. "
            "Fill them in .env and try again."
        )

    if not os.path.isdir(CLEANED_EXPORTS_DIR):
        raise SystemExit(
            f"Folder '{CLEANED_EXPORTS_DIR}' not found. Run Transformer.py first."
        )

    all_xlsx = glob.glob(os.path.join(CLEANED_EXPORTS_DIR, "*.xlsx"))
    files = [f for f in all_xlsx if ODOO_FILE_MARKER in os.path.basename(f)]
    if not files:
        files = [f for f in all_xlsx if PARKING_FILE_MARKER in os.path.basename(f)]
    if not files:
        raise SystemExit(
            f"No Parking Transactions / Odoo .xlsx files in '{CLEANED_EXPORTS_DIR}'. "
            "Run Transformer.py first."
        )

    print("Getting access token...")
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    print("Resolving SharePoint site...")
    site_id = get_site_id(headers)

    print(f"Ensuring folder path exists: {TARGET_FOLDER_PATH}")
    ensure_folder_path(site_id, headers)

    print(f"Uploading {len(files)} file(s) to '{TARGET_FOLDER_PATH}'...")
    for fp in files:
        try:
            result = upload_file(site_id, headers, fp)
            print(f"  OK: {os.path.basename(fp)} -> {result.get('webUrl', 'uploaded')}")
            os.remove(fp)
        except requests.RequestException as e:
            print(f"  FAIL: {os.path.basename(fp)} - {e}")
            raise

    print("Done.")


if __name__ == "__main__":
    main()
