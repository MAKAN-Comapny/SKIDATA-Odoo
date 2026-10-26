"""
Upload transformed Parking Transactions (*-Odoo.xlsx) from Cleaned-Exports/
to Microsoft SharePoint folder TARGET_FOLDER_PATH_ODOO (default: Odoo_testing_Sumit for dev).

Run Transformer.py (or main.py) first so Cleaned-Exports/ is populated.

Note: TARGET_FOLDER_PATH is the raw daily source folder (Skidata_Daily_Files).
Do not upload Odoo files there — use TARGET_FOLDER_PATH_ODOO instead.
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
# Destination for transformed *-Odoo.xlsx (NOT the raw daily folder).
# dev branch defaults to Odoo_testing_Sumit for isolated staging.
TARGET_FOLDER_PATH = (
    os.getenv("TARGET_FOLDER_PATH_ODOO")
    or os.getenv("ODOO_SHAREPOINT_FOLDER")
    or "Odoo_testing_Sumit"
).strip()

CLEANED_EXPORTS_DIR = "Cleaned-Exports"
ODOO_FILE_MARKER = "Parking-Transactions-Odoo"
PARKING_FILE_MARKER = "Parking-Transactions"


def encode_drive_path(path: str) -> str:
    """URL-encode each folder/file segment for Graph drive paths."""
    return "/".join(requests.utils.quote(part, safe="") for part in path.split("/") if part)


def get_folder_item(site_id, headers, folder_path: str) -> dict:
    """Resolve a folder by path; returns the drive item metadata."""
    encoded = encode_drive_path(folder_path)
    url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{encoded}"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


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


def ensure_folder_path(site_id, headers, folder_path: str | None = None):
    """Create nested folders if they don't exist."""
    target = folder_path or TARGET_FOLDER_PATH
    parts = [p for p in target.split("/") if p.strip()]
    current = ""

    for part in parts:
        current = f"{current}/{part}" if current else part
        encoded = encode_drive_path(current)
        check = requests.get(
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{encoded}",
            headers=headers,
        )

        if check.status_code == 404:
            parent = current.rsplit("/", 1)[0] if "/" in current else ""
            encoded_parent = encode_drive_path(parent) if parent else ""
            create_url = (
                f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:/{encoded_parent}:/children"
                if parent
                else f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root/children"
            )
            folder_payload = {
                "name": part,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "replace",
            }
            requests.post(create_url, headers=headers, json=folder_payload)


def upload_file(site_id, headers, file_path, folder_path: str | None = None):
    """Upload a single file to SharePoint. Uses upload session if > 4MB."""
    folder_path = folder_path or TARGET_FOLDER_PATH
    file_name = os.path.basename(file_path)
    encoded_name = requests.utils.quote(file_name, safe="")
    file_size = os.path.getsize(file_path)

    # For files > 4MB, use Graph upload session to avoid 413 error
    if file_size > 4 * 1024 * 1024:
        encoded_folder = encode_drive_path(folder_path)
        session_url = (
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root:"
            f"/{encoded_folder}/{encoded_name}:/createUploadSession"
        )
        payload = {
            "item": {
                "@microsoft.graph.conflictBehavior": "replace",
                "name": file_name,
            }
        }
        r = requests.post(session_url, headers=headers, json=payload)
        r.raise_for_status()
        upload_url = r.json()["uploadUrl"]

        chunk_size = 10 * 1024 * 1024
        with open(file_path, "rb") as f:
            start = 0
            while start < file_size:
                chunk = f.read(chunk_size)
                end = start + len(chunk) - 1
                chunk_headers = {
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Length": str(len(chunk)),
                }
                res = requests.put(upload_url, headers=chunk_headers, data=chunk)
                res.raise_for_status()
                start += len(chunk)
        return res.json()
    else:
        folder_item = get_folder_item(site_id, headers, folder_path)
        folder_id = folder_item["id"]
        upload_url = (
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{folder_id}:"
            f"/{encoded_name}:/content"
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
