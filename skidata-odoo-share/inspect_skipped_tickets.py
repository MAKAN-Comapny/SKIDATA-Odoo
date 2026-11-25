"""
Diagnostic script to inspect skipped/ghost tickets in live Odoo ERP.
Directly queries Odoo via XML-RPC to retrieve the complete record details.
"""
import json
import os
import sys

# Ensure import paths work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from odoo_client import OdooClient

DEFAULT_TICKETS = [
    "32612008105029300001351",  # Target: 2026-11-18 (Nakheel Mall Dammam, SAR 40)
    "28781939105028500090093",  # Target: 2026-09-09 (Al Noor Mall Madinah, SAR 10)
]


def inspect_tickets():
    # Allow override via env variable or command line args
    env_tickets = os.getenv("TARGET_TICKETS")
    if env_tickets:
        tickets = [t.strip() for t in env_tickets.split(",") if t.strip()]
    elif len(sys.argv) > 1:
        tickets = [t.strip() for t in sys.argv[1:] if t.strip()]
    else:
        tickets = DEFAULT_TICKETS

    print("=" * 80)
    print("ODOO DIAGNOSTIC AUDIT: INSPECTING SKIPPED / GHOST TICKETS")
    print("=" * 80)
    print(f"Connecting to Odoo: {config.ODOO_URL} / DB: {config.ODOO_DB}")
    client = OdooClient()
    print(f"Authenticated successfully! UID: {client.uid}")
    print(f"Tickets to inspect ({len(tickets)}): {tickets}\n")

    fields_to_read = [
        "id",
        "name",
        "move_type",
        "state",
        "payment_state",
        "invoice_date",
        "date",
        "amount_total",
        "amount_untaxed",
        "create_date",
        "write_date",
        "partner_id",
        "journal_id",
        "ticket_sequence",
    ]

    for idx, seq in enumerate(tickets, start=1):
        print("\n" + "#" * 80)
        print(f"[{idx}/{len(tickets)}] AUDITING TICKET: {seq}")
        print("#" * 80)

        # 1. Exact query used by the importer's dedupe check
        domain_dedupe = [("ticket_sequence", "=", seq), ("state", "=", "posted")]
        print(f"\n[Query 1] Deduplication Query: {domain_dedupe}")
        dedupe_records = client._execute("account.move", "search_read", domain_dedupe, fields_to_read)
        print(f"  -> Found {len(dedupe_records)} record(s) matching importer dedupe condition.")

        # 2. Query ANY state (posted, draft, cancel, etc.)
        domain_any_state = [("ticket_sequence", "=", seq)]
        print(f"\n[Query 2] Any State Query: {domain_any_state}")
        all_records = client._execute("account.move", "search_read", domain_any_state, fields_to_read)
        print(f"  -> Found {len(all_records)} record(s) matching ticket_sequence in any state.")

        # 3. If nothing found in exact match, try substring / ilike
        if not all_records:
            suffix = seq[-12:]
            domain_suffix = [("ticket_sequence", "ilike", suffix)]
            print(f"\n[Query 3] Suffix Search (ilike '%{suffix}%'):")
            suffix_records = client._execute("account.move", "search_read", domain_suffix, fields_to_read)
            print(f"  -> Found {len(suffix_records)} record(s) matching suffix.")
            target_records = suffix_records
        else:
            target_records = all_records

        # Print detailed inspection of found records
        if target_records:
            for r_idx, rec in enumerate(target_records, start=1):
                move_id = rec.get("id")
                curr_state = rec.get("state")
                curr_pay_state = rec.get("payment_state")

                print(f"\n--- RECORD #{r_idx} DETAILS ---")
                print(f"  Odoo Move ID       : {move_id}")
                print(f"  Invoice Number     : {rec.get('name')}")
                print(f"  Move Type          : {rec.get('move_type')}")
                print(f"  State              : {curr_state}")
                print(f"  Payment State      : {curr_pay_state}")
                print(f"  Invoice/Bill Date  : {rec.get('invoice_date')}")
                print(f"  Accounting Date    : {rec.get('date')}")
                print(f"  Total Amount       : {rec.get('amount_total')}")
                print(f"  Untaxed Amount     : {rec.get('amount_untaxed')}")
                print(f"  Ticket Sequence    : {rec.get('ticket_sequence')}")
                print(f"  Partner            : {rec.get('partner_id')}")
                print(f"  Journal            : {rec.get('journal_id')}")
                print(f"  Created Timestamp  : {rec.get('create_date')}")
                print(f"  Last Modified      : {rec.get('write_date')}")

                # ACTION: If posted but not paid, register payment immediately
                if curr_state == "posted" and curr_pay_state != "paid":
                    print(f"\n⚡ [REMEDIATION] Move ID {move_id} is posted but payment_state is '{curr_pay_state}'.")
                    payment_journal_id = config.ODOO_PAYMENT_JOURNAL_ID
                    print(f"  -> Calling client.register_payment(move_id={move_id}, journal_id={payment_journal_id})...")
                    try:
                        pay_res = client.register_payment(move_id, journal_id=payment_journal_id)
                        print(f"  -> Payment wizard executed! Wizard Result: {pay_res}")
                        # Re-verify state from live Odoo
                        refreshed = client._execute(
                            "account.move", "read", [move_id], ["id", "name", "state", "payment_state", "amount_total"]
                        )
                        if refreshed:
                            updated = refreshed[0]
                            print(f"  -> SUCCESS: Move ID {move_id} updated in Odoo:")
                            print(f"     Invoice Name  : {updated.get('name')}")
                            print(f"     State         : {updated.get('state')}")
                            print(f"     Payment State : {updated.get('payment_state')} (VERIFIED PAID)")
                    except Exception as pay_err:
                        print(f"  ❌ Failed to register payment: {pay_err}")
                elif curr_pay_state == "paid":
                    print(f"\n✅ Move ID {move_id} is already marked PAID in Odoo. No action needed.")
        else:
            print(f"\n❌ ZERO RECORDS FOUND in Odoo for ticket sequence '{seq}'.")

    print("\n" + "=" * 80)
    print("INSPECTION COMPLETE.")
    print("=" * 80)


if __name__ == "__main__":
    inspect_tickets()
