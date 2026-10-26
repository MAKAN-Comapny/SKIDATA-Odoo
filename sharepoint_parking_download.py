"""
Download the latest raw Parking Transactions report from SharePoint
folder Skidata_Daily_Files (or TARGET_FOLDER_PATH) into exports/.

Used by main.py instead of the SKIDATA portal scraper.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

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


def encode_drive_path(path: str) -> str:
    """URL-encode each folder/file segment for Graph drive paths."""
    return "/".join(requests.utils.quote(part, safe="") for part in path.split("/") if part)


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
    encoded_path = encode_drive_path(folder_path)
    url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{encoded_path}:/children"
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code == 404:
        # Fallback between Skidata_Daily_Files and Ski-data-daily
        alt_folder = None
        clean_name = folder_path.lower().replace(" ", "").replace("-", "").replace("_", "")
        if "skidata" in clean_name and "daily" in clean_name:
            if folder_path == "Skidata_Daily_Files":
                alt_folder = "Ski-data-daily"
            else:
                alt_folder = "Skidata_Daily_Files"
        if alt_folder:
            print(f"Folder '{folder_path}' not found (404), trying alternate '{alt_folder}'...")
            alt_url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{encode_drive_path(alt_folder)}:/children"
            r2 = requests.get(alt_url, headers=headers, timeout=30)
            if r2.status_code == 200:
                return r2.json().get("value", [])
        raise RuntimeError(f"SharePoint folder not found: {folder_path}")
    r.raise_for_status()
    return r.json().get("value", [])


def target_date_prefixes() -> list[str]:
    """TARGET_DATE=YYYY-MM-DD selects that day's file by name, e.g. '20-Oct-26-' or '20-Oct-2026-'."""
    raw = os.getenv("TARGET_DATE", "").strip()
    if not raw:
        return []
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return [
            dt.strftime("%d-%b-%y") + "-",
            dt.strftime("%d-%b-%Y") + "-",
            dt.strftime("%Y-%m-%d") + "-",
        ]
    except ValueError:
        return [raw + "-"]


def pick_latest_parking(items: list, folder_path: str = "") -> dict:
    """
    Prefer raw Parking-Transactions files (exclude *-Odoo*).
    Newest by lastModifiedDateTime, or with TARGET_DATE set, the file for that day.
    """
    prefixes = target_date_prefixes()
    names = [i.get("name", "") for i in items if i.get("file")]
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
        if prefixes and not any(name.startswith(p) for p in prefixes):
            continue
        candidates.append(item)

    if not candidates:
        preview = ", ".join(names[:20]) if names else "(empty folder)"
        if prefixes:
            raise RuntimeError(
                f"No '{prefixes[0]}{PARKING_MARKER}' .xlsx found in SharePoint folder "
                f"'{folder_path}'. Files seen: {preview}."
            )
        raise RuntimeError(
            f"No '{PARKING_MARKER}' .xlsx found in SharePoint folder "
            f"'{folder_path}' (excluding *-Odoo*). "
            f"Files seen: {preview}. "
            f"Set TARGET_FOLDER_PATH=Skidata_Daily_Files "
            f"(not SKIDATA - Odoo)."
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
    # Prefer explicit SOURCE folder; TARGET_FOLDER_PATH may still be the old
    # "SKIDATA - Odoo" value in GitHub secrets — fall back to daily files.
    configured = (
        os.getenv("SHAREPOINT_SOURCE_FOLDER")
        or os.getenv("TARGET_FOLDER_PATH")
        or DEFAULT_SOURCE_FOLDER
    ).strip()
    source_folder = configured
    if configured.lower().replace(" ", "") in {"skidata-odoo", "skidataodoo"} or (
        "odoo" in configured.lower() and "daily" not in configured.lower()
    ):
        print(
            f"WARNING: TARGET_FOLDER_PATH={configured!r} looks like the Odoo "
            f"upload folder. Using {DEFAULT_SOURCE_FOLDER!r} for raw download."
        )
        source_folder = DEFAULT_SOURCE_FOLDER

    print(f"SharePoint source folder: {source_folder}")
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    site_id = get_site_id(headers)

    items = list_folder_files(headers, site_id, source_folder)
    file_names = [i.get("name") for i in items if i.get("file")]
    print(f"Files in folder ({len(file_names)}): {file_names}")
    latest = pick_latest_parking(items, folder_path=source_folder)
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
    print(f"Downloaded to: {dest_path} ({os.path.getsize(dest_path):,} bytes)")
    return dest_path


if __name__ == "__main__":
    fetch_latest_parking_to_exports()
