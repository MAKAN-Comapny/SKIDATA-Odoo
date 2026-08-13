"""
Unattended SharePoint -> Odoo draft import.

Used by GitHub Actions / cron. No interactive prompt.
"""
import os
import sys

import import_csv_batch
import odoo_client
import sharepoint_download


def _import_limit():
    raw = os.getenv("IMPORT_LIMIT", "").strip()
    if not raw:
        return None
    return int(raw)


def run():
    print("=" * 64)
    print("SKIDATA-Odoo import: SharePoint latest file -> Odoo drafts")
    print("=" * 64)

    dest_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
    local_path, item, folder = sharepoint_download.fetch_latest_export(dest_dir=dest_dir)
    if local_path is None:
        print("No matching export found in SharePoint.")
        print("Tried folders:", folder)
        sys.exit(1)

    print(f"Folder : {folder}")
    print(f"File   : {item.get('name')}")
    print(f"Modified: {item.get('lastModifiedDateTime')}")
    print(f"Local  : {local_path}")

    limit = _import_limit()
    rows = import_csv_batch.load_rows(local_path, limit)
    print(f"Rows   : {len(rows)}" + (f" (IMPORT_LIMIT={limit})" if limit else ""))
    print("Mode   : DRAFT invoices only (no post)")

    payloads = []
    build_failed = []
    for row in rows:
        ticket = str(row.get("Ticket Sequence", "")).strip()
        try:
            payloads.append((ticket, import_csv_batch.build_payload(row)))
        except Exception as exc:  # noqa: BLE001
            build_failed.append({"ticket_sequence": ticket, "error": str(exc)})

    if build_failed:
        print(f"Payload build failed: {len(build_failed)}")
        for item_fail in build_failed[:20]:
            print(f"  - {item_fail}")

    print(f"Creating/checking {len(payloads)} invoice(s) in Odoo...")
    client = odoo_client.OdooClient()
    results = client.process_batch(payloads)

    created = results["created"]
    skipped = results["skipped_duplicate"]
    failed = results["failed"]

    print("\n=== Results ===")
    print(f"Created : {len(created)}")
    print(f"Skipped : {len(skipped)} (already exist)")
    print(f"Failed  : {len(failed)}")
    if created[:5]:
        print("Sample created:", created[:5])
    if failed[:20]:
        print("Failures:")
        for item_fail in failed[:20]:
            print(f"  - {item_fail}")

    if failed or build_failed:
        sys.exit(1)

    print("Import complete.")


if __name__ == "__main__":
    run()
