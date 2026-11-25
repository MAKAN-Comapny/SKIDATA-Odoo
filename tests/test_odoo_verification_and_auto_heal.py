"""
Unit tests for Post-Import Verification Gate and Auto-Heal Engine in main_import.py.
Verifies:
1. Immediate pass when 100% of tickets exist in Odoo (0 missing).
2. Auto-heal recovery when transient gateway timeouts cause tickets to drop initially.
3. Multi-pass auto-heal behavior.
4. Permanent failure handling: audit CSV generation and FAIL status reporting.
5. Handling of empty or blank ticket sequences.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SHARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skidata-odoo-share"
)
if SHARE_DIR not in sys.path:
    sys.path.insert(0, SHARE_DIR)

import main_import


class TestOdooVerificationAndAutoHeal(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()

    def test_immediate_pass_all_verified(self):
        """When live Odoo has all tickets, verification passes immediately with 0 retries."""
        payloads = [
            ("tkt_001", {"ticket_sequence": "tkt_001", "amount": 10}),
            ("tkt_002", {"ticket_sequence": "tkt_002", "amount": 20}),
            ("tkt_003", {"ticket_sequence": "tkt_003", "amount": 30}),
        ]
        # Odoo returns all 3 tickets as confirmed existing
        self.mock_client.get_existing_ticket_sequences.return_value = {
            "tkt_001",
            "tkt_002",
            "tkt_003",
        }

        res = main_import.verify_and_auto_heal(
            client=self.mock_client,
            payloads=payloads,
            post=True,
            pay=True,
        )

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["expected_count"], 3)
        self.assertEqual(res["verified_count"], 3)
        self.assertEqual(res["missing_tickets"], [])
        self.assertEqual(res["healed_records"], [])
        # Only 1 initial verification call was made, no retry calls
        self.assertEqual(self.mock_client.get_existing_ticket_sequences.call_count, 1)
        self.assertEqual(self.mock_client.process_batch.call_count, 0)

    @patch("time.sleep", return_value=None)
    def test_auto_heal_recovers_missing_tickets(self, mock_sleep):
        """When 2 tickets are missing due to gateway timeout, auto-heal re-processes and verifies them."""
        payloads = [
            ("tkt_100", {"ticket_sequence": "tkt_100", "amount": 10}),
            ("tkt_101", {"ticket_sequence": "tkt_101", "amount": 40}),
            ("tkt_102", {"ticket_sequence": "tkt_102", "amount": 10}),
        ]

        # Initial check: tkt_101 and tkt_102 are missing in Odoo
        # After auto-heal pass 1: reverify returns all missing tickets as confirmed!
        self.mock_client.get_existing_ticket_sequences.side_effect = [
            {"tkt_100"},  # Initial check: only tkt_100 found
            {"tkt_101", "tkt_102"},  # Re-verify check: both recovered!
        ]

        self.mock_client.process_batch.return_value = {
            "created": [
                {"ticket_sequence": "tkt_101", "move_id": 901, "posted": True, "paid": True},
                {"ticket_sequence": "tkt_102", "move_id": 902, "posted": True, "paid": True},
            ],
            "skipped_duplicate": [],
            "failed": [],
        }

        res = main_import.verify_and_auto_heal(
            client=self.mock_client,
            payloads=payloads,
            post=True,
            pay=True,
            payment_journal_id=99,
            max_retries=3,
            base_backoff=0.01,
            retry_pacing=0.01,
        )

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["expected_count"], 3)
        self.assertEqual(res["verified_count"], 3)
        self.assertEqual(res["missing_tickets"], [])
        self.assertEqual(len(res["healed_records"]), 2)
        # process_batch was called once for the 2 missing tickets
        self.assertEqual(self.mock_client.process_batch.call_count, 1)
        called_payloads = self.mock_client.process_batch.call_args[0][0]
        called_keys = {p[0] for p in called_payloads}
        self.assertEqual(called_keys, {"tkt_101", "tkt_102"})

    @patch("time.sleep", return_value=None)
    def test_auto_heal_multi_pass_recovery(self, mock_sleep):
        """Pass 1 recovers 1 of 2 tickets; pass 2 recovers the remaining ticket."""
        payloads = [
            ("tkt_A", {"ticket_sequence": "tkt_A"}),
            ("tkt_B", {"ticket_sequence": "tkt_B"}),
            ("tkt_C", {"ticket_sequence": "tkt_C"}),
        ]

        self.mock_client.get_existing_ticket_sequences.side_effect = [
            {"tkt_A"},  # Initial: tkt_B and tkt_C missing
            {"tkt_B"},  # After pass 1: only tkt_B recovered, tkt_C still missing
            {"tkt_C"},  # After pass 2: tkt_C recovered!
        ]

        self.mock_client.process_batch.side_effect = [
            {"created": [{"ticket_sequence": "tkt_B"}], "skipped_duplicate": [], "failed": []},
            {"created": [{"ticket_sequence": "tkt_C"}], "skipped_duplicate": [], "failed": []},
        ]

        res = main_import.verify_and_auto_heal(
            client=self.mock_client,
            payloads=payloads,
            max_retries=3,
            base_backoff=0.01,
        )

        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["missing_tickets"], [])
        self.assertEqual(len(res["healed_records"]), 2)
        self.assertEqual(self.mock_client.process_batch.call_count, 2)

    @patch("time.sleep", return_value=None)
    def test_permanent_failure_exhausts_and_generates_audit_csv(self, mock_sleep):
        """When a ticket cannot be created after max_retries, it writes audit CSV and returns FAIL."""
        payloads = [
            ("tkt_PERM_FAIL", {"ticket_sequence": "tkt_PERM_FAIL"}),
        ]

        # Initial check: missing
        # Pass 1 re-verify: still missing
        # Pass 2 re-verify: still missing
        self.mock_client.get_existing_ticket_sequences.return_value = set()
        self.mock_client.process_batch.return_value = {
            "created": [],
            "skipped_duplicate": [],
            "failed": [{"ticket_sequence": "tkt_PERM_FAIL", "error": "Fatal accounting error"}],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            res = main_import.verify_and_auto_heal(
                client=self.mock_client,
                payloads=payloads,
                max_retries=2,
                base_backoff=0.01,
                audit_output_dir=tmpdir,
            )

            self.assertEqual(res["status"], "FAIL")
            self.assertEqual(res["missing_tickets"], ["tkt_PERM_FAIL"])
            self.assertIn("audit_file", res)
            self.assertTrue(os.path.exists(res["audit_file"]))
            # Read audit CSV and check contents
            with open(res["audit_file"], "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("tkt_PERM_FAIL", content)
            self.assertIn("MISSING_AFTER_AUTO_HEAL", content)

    def test_empty_payloads_handled_cleanly(self):
        """Empty payloads list returns PASS without error."""
        res = main_import.verify_and_auto_heal(
            client=self.mock_client,
            payloads=[],
        )
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["expected_count"], 0)
        self.assertEqual(res["missing_tickets"], [])
        self.assertEqual(self.mock_client.get_existing_ticket_sequences.call_count, 0)

    def test_verification_gate_passes_require_paid_flag(self):
        """When pay=True, verify_and_auto_heal must require invoices to be both posted and paid."""
        payloads = [("tkt_PAID_REQ", {"ticket_sequence": "tkt_PAID_REQ"})]
        self.mock_client.get_existing_ticket_sequences.return_value = {"tkt_PAID_REQ"}

        main_import.verify_and_auto_heal(
            client=self.mock_client,
            payloads=payloads,
            post=True,
            pay=True,
        )

        # Check call arguments to get_existing_ticket_sequences
        self.mock_client.get_existing_ticket_sequences.assert_called_with(
            ["tkt_PAID_REQ"], chunk_size=1000, only_posted=True, require_paid=True
        )


if __name__ == "__main__":
    unittest.main()
