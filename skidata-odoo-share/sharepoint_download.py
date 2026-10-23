"""
Download the latest pre-mapped Odoo export from SharePoint.

Uses the same Microsoft Graph app credentials as SKIDATA-Odoo upload
(TENANT_ID / CLIENT_ID / CLIENT_SECRET / SHAREPOINT_SITE_NAME).
"""
import os
import re
from datetime import datetime

import requests

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_FOLDER_CANDIDATES = ("SKIDATA - Odoo", "SKIDATA-Odoo")
# Only transformed Odoo files — never raw Parking-Transactions.xlsx
FILENAME_MARKERS = ("Parking-Transactions-Odoo",)


def _env(name, default=None):
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value).strip()


def get_access_token():
    token_url = f"https://login.microsoftonline.com/{_env('TENANT_ID')}/oauth2/v2.0/token"
    data = {
        "client_id": _env("CLIENT_ID"),
        "client_secret": _env("CLIENT_SECRET"),
        "grant_type": "client_credentials",
        "scope": "https://graph.microsoft.com/.default",
    }
    r = requests.post(token_url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def get_site_id(headers):
    site_input = _env("SHAREPOINT_SITE_NAME")
    url_match = re.match(r"https?://([^/]+)(/.*)?", site_input)
    if url_match:
        hostname = url_match.group(1)
        path = (url_match.group(2) or "/").rstrip("/") or "/"
        if not path.startswith("/"):
            path = "/" + path
        get_url = f"{GRAPH_BASE}/sites/{hostname}:{path}"
        r = requests.get(get_url, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()["id"]

    search_url = f"{GRAPH_BASE}/sites?search={requests.utils.quote(site_input)}"
    r = requests.get(search_url, headers=headers, timeout=30)
    r.raise_for_status()
    sites = r.json().get("value", [])
    if not sites:
        raise ValueError(f"No site found with name or URL: {site_input}")
    return sites[0]["id"]


def folder_candidates():
    """
    Import must read from the Odoo upload folder only.
    Prefer TARGET_FOLDER_PATH_ODOO; never treat Skidata_Daily_Files as source.
    """
    folders = []
    for key in ("TARGET_FOLDER_PATH_ODOO", "ODOO_SHAREPOINT_FOLDER"):
        value = os.getenv(key, "").strip()
        if value and value not in folders:
            folders.append(value)

    # Legacy: TARGET_FOLDER_PATH was once "SKIDATA - Odoo". Ignore if it now
    # points at the raw daily folder.
    legacy = os.getenv("TARGET_FOLDER_PATH", "").strip()
    if legacy:
        legacy_norm = legacy.lower().replace(" ", "")
        if "daily" in legacy_norm or legacy_norm == "skidatadailyfiles":
            print(
                f"Ignoring TARGET_FOLDER_PATH={legacy!r} for Odoo import "
                f"(raw daily folder)."
            )
        elif legacy not in folders:
            folders.append(legacy)

    for name in DEFAULT_FOLDER_CANDIDATES:
        if name not in folders:
            folders.append(name)
    return folders


def list_folder_files(headers, site_id, folder_path):
    url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{folder_path}:/children"
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("value", [])


def target_date_prefix():
    """TARGET_DATE=YYYY-MM-DD selects that day's *-Odoo file by name, e.g. '20-Oct-26-'."""
    raw = os.getenv("TARGET_DATE", "").strip()
    if not raw:
        return None
    return datetime.strptime(raw, "%Y-%m-%d").strftime("%d-%b-%y") + "-"


def pick_latest_export(items):
    prefix = target_date_prefix()
    candidates = [
        item
        for item in items
        if item.get("file")
        and item.get("name", "").lower().endswith((".xlsx", ".csv"))
        and any(marker.lower() in item["name"].lower() for marker in FILENAME_MARKERS)
        and (not prefix or item["name"].startswith(prefix))
    ]
    if not candidates:
        names = [i.get("name") for i in items if i.get("file")]
        print(f"No *-Parking-Transactions-Odoo* file found. Files seen: {names}")
        return None
    candidates.sort(key=lambda i: i.get("lastModifiedDateTime", ""), reverse=True)
    return candidates[0]


def download_file(headers, item, dest_path):
    download_url = item.get("@microsoft.graph.downloadUrl")
    if not download_url:
        raise RuntimeError(f"No download URL for SharePoint item: {item.get('name')}")
    r = requests.get(download_url, headers=headers, timeout=120)
    r.raise_for_status()
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(r.content)
    return dest_path


def fetch_latest_export(dest_dir="downloads"):
    """
    Find and download the newest matching export.
    Returns (local_path, file_item, folder_used) or (None, None, tried_folders).
    """
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    site_id = get_site_id(headers)

    tried = []
    for folder in folder_candidates():
        tried.append(folder)
        items = list_folder_files(headers, site_id, folder)
        if items is None:
            continue
        latest = pick_latest_export(items)
        if latest is None:
            continue
        dest_path = os.path.join(dest_dir, latest["name"])
        download_file(headers, latest, dest_path)
        return dest_path, latest, folder

    return None, None, tried
