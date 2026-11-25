"""
Unattended SharePoint / Local Cleaned-Exports -> Odoo draft import.

Used by GitHub Actions / cron. No interactive prompt.
"""
import csv
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


def verify_and_auto_heal(
    client,
    payloads: list,
    post: bool = False,
    pay: bool = False,
    payment_journal_id: int | None = None,
    max_retries: int = 3,
    base_backoff: float = 5.0,
    retry_pacing: float = 0.15,
    audit_output_dir: str | None = None,
) -> dict:
    """
    Post-Import Verification Gate & Auto-Heal Engine:
    Queries live Odoo ERP to verify that 100% of transformed ticket sequences
    exist in the database. If any tickets are missing (due to transient HTTP
    502/504 gateway drops or database locks), executes targeted retry passes
    with exponential backoff and pacing until 100% parity is verified or max_retries exhausted.
    """
    clean_sequences = [
        str(p[0]).strip() for p in payloads if p[0] and str(p[0]).strip()
    ]
    unique_expected = list(dict.fromkeys(clean_sequences))
    if not unique_expected:
        print("\n[Post-Import Gate] No valid ticket sequences to verify.")
        return {
            "status": "PASS",
            "expected_count": 0,
            "verified_count": 0,
            "missing_tickets": [],
            "healed_records": [],
        }

    print("\n" + "=" * 64)
    print(f"[Post-Import Gate] Verifying {len(unique_expected)} ticket(s) in live Odoo ERP...")
    print("=" * 64)

    # 1. Query Odoo for all expected ticket sequences in chunks of 1000
    confirmed = client.get_existing_ticket_sequences(unique_expected, chunk_size=1000)
    missing = set(unique_expected) - confirmed

    if not missing:
        print(f"[Post-Import Gate] PASSED: All {len(unique_expected):,} tickets verified present in Odoo (0 missing).")
        return {
            "status": "PASS",
            "expected_count": len(unique_expected),
            "verified_count": len(confirmed),
            "missing_tickets": [],
            "healed_records": [],
        }

    print(f"\n[ALERT] Discrepancy detected: {len(missing)} of {len(unique_expected)} ticket(s) MISSING in Odoo!")
    print(f"Sample missing ticket(s): {list(missing)[:5]}")

    # Build lookup map for fast payload retrieval by ticket sequence
    payload_map = {}
    for p in payloads:
        seq = str(p[0]).strip()
        if seq in missing and seq not in payload_map:
            payload_map[seq] = p

    healed_records = []

    # 2. Multi-Pass Auto-Heal Loop
    for attempt in range(1, max_retries + 1):
        backoff = base_backoff * attempt
        print(f"\n[Auto-Heal Pass {attempt}/{max_retries}] Pausing {backoff:.1f}s for gateway recovery...")
        time.sleep(backoff)

        retry_payloads = [payload_map[seq] for seq in missing if seq in payload_map]
        print(
            f"[Auto-Heal Pass {attempt}/{max_retries}] Retrying {len(retry_payloads)} missing ticket(s) (pacing={retry_pacing}s)..."
        )

        heal_results = client.process_batch(
            retry_payloads,
            post=post,
            pay=pay,
            payment_journal_id=payment_journal_id,
            pacing_delay=retry_pacing,
        )
        healed_records.extend(heal_results.get("created", []))

        # Re-verify remaining missing tickets against live Odoo
        reverified = client.get_existing_ticket_sequences(list(missing), chunk_size=1000)
        newly_healed = missing.intersection(reverified)
        missing = missing - reverified

        print(
            f"[Auto-Heal Pass {attempt}/{max_retries}] Result: Recovered {len(newly_healed)} ticket(s). "
            f"Remaining missing: {len(missing)}."
        )

        if not missing:
            print("[Post-Import Gate] AUTO-HEAL COMPLETE: All tickets successfully verified in Odoo ERP.")
            return {
                "status": "PASS",
                "expected_count": len(unique_expected),
                "verified_count": len(unique_expected),
                "missing_tickets": [],
                "healed_records": healed_records,
            }

    # 3. If tickets remain missing after all auto-heal passes, generate audit file
    print(f"\n[CRITICAL ERROR] Auto-heal exhausted: {len(missing)} ticket(s) could NOT be ingested into Odoo!")

    out_dir = audit_output_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    audit_file = os.path.join(out_dir, f"Failed_To_Odoo_{timestamp}.csv")
    try:
        with open(audit_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ticket_sequence", "status", "timestamp"])
            for m_seq in sorted(missing):
                writer.writerow([m_seq, "MISSING_AFTER_AUTO_HEAL", timestamp])
        print(f"[Audit Report Generated] Saved missing tickets list to: {audit_file}")
    except Exception as exc:
        print(f"Warning: Failed to save audit file: {exc}")

    return {
        "status": "FAIL",
        "expected_count": len(unique_expected),
        "verified_count": len(unique_expected) - len(missing),
        "missing_tickets": list(missing),
        "healed_records": healed_records,
        "audit_file": audit_file,
    }


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

    # 4. Post-Import Verification Gate & Auto-Heal Engine
    verify_res = verify_and_auto_heal(
        client=client,
        payloads=payloads,
        post=post,
        pay=pay,
        payment_journal_id=payment_journal_id,
        max_retries=3,
        audit_output_dir=os.path.dirname(local_path) if local_path else None,
    )

    if verify_res.get("healed_records"):
        created.extend(verify_res["healed_records"])

    final_missing = set(verify_res.get("missing_tickets", []))

    print("\n=== Final Verification Results ===")
    print(f"Total Unique Expected : {verify_res['expected_count']}")
    print(f"Verified in Odoo      : {verify_res['verified_count']}")
    print(f"Created (This Run)    : {len(created)}")
    print(f"Skipped (Pre-existing): {len(skipped)}")
    print(f"Permanently Failed    : {len(final_missing)}")
    if created[:5]:
        print("Sample created:", created[:5])
    if final_missing:
        print("Missing tickets in Odoo:")
        for m in list(final_missing)[:20]:
            print(f"  - {m}")

    if verify_res["status"] == "FAIL" or build_failed:
        print("\n[FAILED] Pipeline halted: One or more tickets failed Odoo verification.")
        sys.exit(1)

    print("\nImport complete: 100% verified in Odoo ERP.")


if __name__ == "__main__":
    run()
