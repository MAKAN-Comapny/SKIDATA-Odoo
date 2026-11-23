"""
Unattended SharePoint / Local Cleaned-Exports -> Odoo draft import.

Used by GitHub Actions / cron. No interactive prompt.
"""
import glob
import os
import sys
import time

import config
import import_csv_batch
import odoo_client
import sharepoint_download


def _import_limit():
    raw = os.getenv("IMPORT_LIMIT", "").strip()
    if not raw:
        return None
    return int(raw)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("true", "1", "yes")


def run():
    post = _env_flag("ODOO_POST")
    pay = _env_flag("ODOO_PAY")
    payment_journal_id = None
    if pay:
        post = True
        payment_journal_id = config.ODOO_PAYMENT_JOURNAL_ID
        if not payment_journal_id:
            raise RuntimeError(
                "ODOO_PAY=true requires ODOO_PAYMENT_JOURNAL_ID to be set."
            )

    action_desc = "POSTED + PAID" if pay else "POSTED" if post else "DRAFT"
    print("=" * 64)
    print(f"SKIDATA-Odoo import: Latest file -> Odoo ({action_desc})")
    print("=" * 64)

    local_path = None
    folder = None
    item = {}

    # 1. Check for explicit file override
    explicit_file = os.getenv("INPUT_FILE", "").strip()
    if explicit_file and os.path.isfile(explicit_file):
        local_path = os.path.abspath(explicit_file)
        folder = "Local (INPUT_FILE)"
        item = {"name": os.path.basename(local_path), "lastModifiedDateTime": "local"}
    else:
        # 2. Check for locally generated transformed files (from main.py step)
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cleaned_dirs = [
            os.getenv("CLEANED_EXPORTS_DIR", "").strip(),
            os.path.join(parent_dir, "Cleaned-Exports"),
            os.path.abspath("Cleaned-Exports"),
        ]
        prefixes = sharepoint_download.target_date_prefixes()
        run_tag = os.getenv("RUN_TAG", "").strip().lower()
        for cdir in cleaned_dirs:
            if cdir and os.path.isdir(cdir):
                matches = glob.glob(os.path.join(cdir, "*Parking-Transactions-Odoo.xlsx"))
                # STRICT AUDIT EXCLUSION & FILTERING:
                matches = [
                    f
                    for f in matches
                    if "audit" not in os.path.basename(f).lower()
                    and "summary" not in os.path.basename(f).lower()
                    and (not prefixes or any(p.rstrip("-").lower() in os.path.basename(f).lower() for p in prefixes))
                    and (not run_tag or run_tag in ("none", "all", "false") or run_tag in os.path.basename(f).lower())
                ]
                if matches:
                    matches.sort(key=os.path.getmtime, reverse=True)
                    local_path = matches[0]
                    folder = f"Local ({cdir})"
                    item = {"name": os.path.basename(local_path), "lastModifiedDateTime": "local"}
                    break

    # 3. Fallback to downloading from SharePoint
    if local_path is None:
        dest_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
        local_path, item, folder = sharepoint_download.fetch_latest_export(dest_dir=dest_dir)
        if local_path is None:
            print("No matching export found in SharePoint or Cleaned-Exports.")
            print("Tried folders:", folder)
            sys.exit(1)

    print(f"Folder : {folder}")
    print(f"File   : {item.get('name')}")
    print(f"Modified: {item.get('lastModifiedDateTime')}")
    print(f"Local  : {local_path}")

    limit = _import_limit()
    rows = import_csv_batch.load_rows(local_path, limit)
    print(f"Rows   : {len(rows)}" + (f" (IMPORT_LIMIT={limit})" if limit else ""))
    print(f"Mode   : {action_desc}")

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
    results = client.process_batch(
        payloads, post=post, pay=pay, payment_journal_id=payment_journal_id
    )

    created = results["created"]
    skipped = results["skipped_duplicate"]
    failed = results["failed"]

    # If any tickets failed due to transient gateway hiccups, perform a second pass retry
    if failed:
        print(f"\n[Retry Pass] Attempting to re-process {len(failed)} failed ticket(s) after 5s recovery pause...")
        time.sleep(5.0)
        failed_keys = {item_fail["ticket_sequence"] for item_fail in failed}
        retry_payloads = [p for p in payloads if p[0] in failed_keys]
        retry_results = client.process_batch(
            retry_payloads, post=post, pay=pay, payment_journal_id=payment_journal_id
        )
        created.extend(retry_results["created"])
        skipped.extend(retry_results["skipped_duplicate"])
        failed = retry_results["failed"]
        print(f"[Retry Pass] Recovered created: {len(retry_results['created'])}, duplicate: {len(retry_results['skipped_duplicate'])}, remaining failed: {len(failed)}")

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
