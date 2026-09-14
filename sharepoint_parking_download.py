"""
Download the latest raw Parking Transactions report from SharePoint
folder Skidata_Daily_Files (or TARGET_FOLDER_PATH) into exports/.

Used by main.py instead of the SKIDATA portal scraper.
"""
from __future__ import annotations

import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
EXPORTS_DIR = "exports"
PARKING_MARKER = "Parking-Transactions"
DEFAULT_SOURCE_FOLDER = "Skidata_Daily_Files"


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not str(value).strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value).strip()


def get_access_token() -> str:
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


def get_site_id(headers: dict) -> str:
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


def list_folder_files(headers: dict, site_id: str, folder_path: str) -> list:
    url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{folder_path}:/children"
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code == 404:
        raise RuntimeError(f"SharePoint folder not found: {folder_path}")
    r.raise_for_status()
    return r.json().get("value", [])


def pick_latest_parking(items: list) -> dict:
    """
    Prefer raw Parking-Transactions files (exclude *-Odoo*).
    Newest by lastModifiedDateTime.
    """
    candidates = []
    for item in items:
        if not item.get("file"):
            continue
        name = item.get("name", "")
        if not name.lower().endswith((".xlsx", ".xlsm")):
            continue
        if PARKING_MARKER not in name:
            continue
        if "Odoo" in name:
            continue
        candidates.append(item)

    if not candidates:
        raise RuntimeError(
            f"No '{PARKING_MARKER}' .xlsx found in SharePoint folder "
            f"(excluding *-Odoo*)."
        )
    candidates.sort(key=lambda i: i.get("lastModifiedDateTime", ""), reverse=True)
    return candidates[0]


def download_file(item: dict, dest_path: str) -> str:
    download_url = item.get("@microsoft.graph.downloadUrl")
    if not download_url:
        raise RuntimeError(f"No download URL for: {item.get('name')}")
    r = requests.get(download_url, timeout=180)
    r.raise_for_status()
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(r.content)
    return dest_path


def fetch_latest_parking_to_exports(dest_dir: str | None = None) -> str:
    """
    Download latest raw Parking Transactions from SharePoint into exports/.
    Returns local file path.
    """
    dest_dir = dest_dir or EXPORTS_DIR
    source_folder = (
        os.getenv("TARGET_FOLDER_PATH", DEFAULT_SOURCE_FOLDER) or DEFAULT_SOURCE_FOLDER
    ).strip()

    print(f"SharePoint source folder: {source_folder}")
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    site_id = get_site_id(headers)

    items = list_folder_files(headers, site_id, source_folder)
    latest = pick_latest_parking(items)
    name = latest["name"]
    print(
        f"Latest parking file: {name} "
        f"(modified {latest.get('lastModifiedDateTime', '?')})"
    )

    # Clear previous exports so Transformer only sees this file.
    os.makedirs(dest_dir, exist_ok=True)
    for existing in os.listdir(dest_dir):
        path = os.path.join(dest_dir, existing)
        if os.path.isfile(path) and existing.lower().endswith((".xlsx", ".xlsm", ".csv")):
            os.remove(path)

    dest_path = os.path.join(dest_dir, name)
    download_file(latest, dest_path)
    print(f"Downloaded to: {dest_path} ({os.path.getsize(dest_path)} bytes)")
    return dest_path


if __name__ == "__main__":
    fetch_latest_parking_to_exports()
