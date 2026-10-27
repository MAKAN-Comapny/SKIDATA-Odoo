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
DEFAULT_SOURCE_FOLDER = "Ski Data 5"


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
    Prefer raw Parking-Transactions files (exclude *-Odoo*, audit, and summary files).
    Newest by lastModifiedDateTime, or with TARGET_DATE set, the file matching that day.
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
        if "odoo" in name.lower() or "audit" in name.lower() or "summary" in name.lower():
            continue
        if prefixes and not any(p.rstrip("-").lower() in name.lower() for p in prefixes):
            continue
        candidates.append(item)

    if not candidates:
        preview = ", ".join(names[:20]) if names else "(empty folder)"
        if prefixes:
            raise RuntimeError(
                f"No '{prefixes[0]}' '{PARKING_MARKER}' .xlsx found in SharePoint folder "
                f"'{folder_path}'. Files seen: {preview}."
            )
        raise RuntimeError(
            f"No raw '{PARKING_MARKER}' .xlsx found in SharePoint folder "
            f"'{folder_path}'. Files seen: {preview}."
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


def fetch_previous_seen_tickets(
    headers: dict,
    site_id: str,
    dest_folder: str = "Odoo_testing_Sumit",
    temp_dir: str = "exports/previous_runs",
) -> set[str]:
    """
    Inspect dest_folder on SharePoint for existing *-Odoo.xlsx files.
    Extract 'Ticket Sequence' numbers to simulate SQL MERGE / deduplication against prior runs.
    """
    seen_tickets: set[str] = set()
    try:
        items = list_folder_files(headers, site_id, dest_folder)
        odoo_files = [
            i
            for i in items
            if i.get("file")
            and i.get("name", "").lower().endswith((".xlsx", ".xlsm"))
            and "-odoo" in i.get("name", "").lower()
            and "audit" not in i.get("name", "").lower()
        ]
        if not odoo_files:
            print(f"No previous *-Odoo.xlsx files found in '{dest_folder}'. First test run.")
            return seen_tickets

        # Sort newest first
        odoo_files.sort(key=lambda i: i.get("lastModifiedDateTime", ""), reverse=True)
        latest_prev = odoo_files[0]
        os.makedirs(temp_dir, exist_ok=True)
        local_path = os.path.join(temp_dir, latest_prev["name"])
        print(f"Checking previous run file '{latest_prev['name']}' in '{dest_folder}' for delta comparison...")
        download_file(latest_prev, local_path)

        import pandas as pd
        df_prev = pd.read_excel(local_path, dtype=str)
        ticket_col = None
        for col in df_prev.columns:
            if "ticket" in str(col).lower() and "sequence" in str(col).lower():
                ticket_col = col
                break
        if ticket_col:
            for val in df_prev[ticket_col].dropna():
                cleaned = str(val).strip()
                if cleaned and cleaned.lower() not in ("nan", "none"):
                    seen_tickets.add(cleaned)
            print(f"Loaded {len(seen_tickets):,} previously seen ticket sequence(s) from '{latest_prev['name']}'.")
        else:
            print(f"Warning: 'Ticket Sequence' column not found in '{latest_prev['name']}'.")

        if os.path.exists(local_path):
            os.remove(local_path)

    except Exception as e:
        print(f"Notice: Could not load previous run tickets from '{dest_folder}': {e}")
        print("Continuing with 0 previous tickets (all tickets evaluated as new).")

    return seen_tickets


def fetch_latest_parking_and_seen_tickets(
    source_folder: str | None = None,
    dest_folder: str | None = None,
    dest_dir: str | None = None,
) -> tuple[str, set[str]]:
    """
    Download latest raw Parking Transactions from source_folder and collect
    seen tickets from dest_folder for SQL MERGE delta tracking.
    """
    dest_dir = dest_dir or EXPORTS_DIR
    source = (
        source_folder
        or os.getenv("SHAREPOINT_SOURCE_FOLDER")
        or os.getenv("TARGET_FOLDER_PATH")
        or DEFAULT_SOURCE_FOLDER
    ).strip()
    dest = (
        dest_folder
        or os.getenv("TARGET_FOLDER_PATH_ODOO")
        or "Odoo_testing_Sumit"
    ).strip()

    print(f"SharePoint source folder:      {source}")
    print(f"SharePoint destination folder: {dest}")
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    site_id = get_site_id(headers)

    # 1. Fetch previous tickets for SQL MERGE delta comparison
    seen_tickets = fetch_previous_seen_tickets(headers, site_id, dest_folder=dest)

    # 2. Download latest raw Parking Transactions file
    items = list_folder_files(headers, site_id, source)
    file_names = [i.get("name") for i in items if i.get("file")]
    print(f"Files found in source ({len(file_names)}): {file_names}")
    latest = pick_latest_parking(items, folder_path=source)
    name = latest["name"]
    print(
        f"Selected raw parking file: {name} "
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

    return dest_path, seen_tickets


def fetch_latest_parking_to_exports(dest_dir: str | None = None) -> str:
    """Download latest raw Parking Transactions from SharePoint into exports/."""
    path, _ = fetch_latest_parking_and_seen_tickets(dest_dir=dest_dir)
    return path


if __name__ == "__main__":
    fetch_latest_parking_and_seen_tickets()
