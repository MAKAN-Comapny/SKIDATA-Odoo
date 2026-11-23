"""
Unit tests for fast batch deduplication and process_batch optimizations in OdooClient.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ODOO_URL", "https://mock.odoo.local")
os.environ.setdefault("ODOO_DB", "mock_db")
os.environ.setdefault("ODOO_USERNAME", "mock_user")
os.environ.setdefault("ODOO_API_KEY", "mock_key")
os.environ.setdefault("ODOO_JOURNAL_ID", "1")
os.environ.setdefault("ODOO_SALES_ACCOUNT_ID", "2")
os.environ.setdefault("ODOO_TAX_ID", "3")
os.environ.setdefault("ODOO_WALKIN_PARTNER_ID", "4")

SHARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skidata-odoo-share"
)
if SHARE_DIR not in sys.path:
    sys.path.insert(0, SHARE_DIR)

from odoo_client import OdooClient


class TestOdooBatchDeduplication(unittest.TestCase):
    @patch("odoo_client.xmlrpc.client.ServerProxy")
    def test_get_existing_ticket_sequences_chunking(self, mock_proxy_cls):
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 42

        mock_models = MagicMock()
        # Suppose chunk 1 returns seq_10, chunk 2 returns seq_1050, chunk 3 returns seq_2200
        mock_models.execute_kw.side_effect = [
            [{"ticket_sequence": "seq_10"}, {"ticket_sequence": "seq_20"}],
            [{"ticket_sequence": "seq_1050"}],
            [{"ticket_sequence": "seq_2200"}],
        ]

        mock_proxy_cls.side_effect = lambda url: mock_common if "common" in url else mock_models

        client = OdooClient()
        sequences = [f"seq_{i}" for i in range(2500)]
        existing = client.get_existing_ticket_sequences(sequences, chunk_size=1000)

        self.assertEqual(existing, {"seq_10", "seq_20", "seq_1050", "seq_2200"})
        # 2500 sequences with chunk_size 1000 should result in exactly 3 calls
        self.assertEqual(mock_models.execute_kw.call_count, 3)

    @patch("odoo_client.xmlrpc.client.ServerProxy")
    def test_get_existing_ticket_sequences_empty(self, mock_proxy_cls):
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 42
        mock_models = MagicMock()
        mock_proxy_cls.side_effect = lambda url: mock_common if "common" in url else mock_models

        client = OdooClient()
        existing = client.get_existing_ticket_sequences([])
        self.assertEqual(existing, set())
        self.assertEqual(mock_models.execute_kw.call_count, 0)

    @patch("odoo_client.xmlrpc.client.ServerProxy")
    def test_process_batch_fast_skip_duplicates(self, mock_proxy_cls):
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 42
        mock_models = MagicMock()

        # Batch check returns seq_1 and seq_2
        mock_models.execute_kw.side_effect = [
            # 1. get_existing_ticket_sequences call
            [{"ticket_sequence": "seq_1"}, {"ticket_sequence": "seq_2"}],
            # 2. create_invoice for seq_3
            1003,
            # 3. action_post for seq_3
            True,
            # 4. register_payment wizard create
            55,
            # 5. action_create_payments
            True,
        ]
        mock_proxy_cls.side_effect = lambda url: mock_common if "common" in url else mock_models

        client = OdooClient()
        payloads = [
            ("seq_1", {"ticket_sequence": "seq_1", "amount": 10}),
            ("seq_2", {"ticket_sequence": "seq_2", "amount": 20}),
            ("seq_3", {"ticket_sequence": "seq_3", "amount": 30}),
        ]

        results = client.process_batch(payloads, post=True, pay=True, payment_journal_id=99, pacing_delay=0)

        self.assertEqual(results["skipped_duplicate"], ["seq_1", "seq_2"])
        self.assertEqual(len(results["created"]), 1)
        self.assertEqual(results["created"][0]["ticket_sequence"], "seq_3")
        self.assertTrue(results["created"][0]["posted"])
        self.assertTrue(results["created"][0]["paid"])
        self.assertEqual(results["failed"], [])

    @patch("odoo_client.xmlrpc.client.ServerProxy")
    def test_process_batch_intra_file_duplicate_prevention(self, mock_proxy_cls):
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 42
        mock_models = MagicMock()

        mock_models.execute_kw.side_effect = [
            # 1. get_existing_ticket_sequences: empty initially
            [],
            # 2. create seq_A (first instance)
            501,
        ]
        mock_proxy_cls.side_effect = lambda url: mock_common if "common" in url else mock_models

        client = OdooClient()
        # Input has seq_A twice
        payloads = [
            ("seq_A", {"ticket_sequence": "seq_A", "amount": 10}),
            ("seq_A", {"ticket_sequence": "seq_A", "amount": 10}),
        ]

        results = client.process_batch(payloads, post=False, pay=False, pacing_delay=0)

        # First instance is created
        self.assertEqual(len(results["created"]), 1)
        self.assertEqual(results["created"][0]["ticket_sequence"], "seq_A")
        # Second instance is automatically detected as intra-batch duplicate and skipped!
        self.assertEqual(results["skipped_duplicate"], ["seq_A"])
        self.assertEqual(results["failed"], [])

    @patch("odoo_client.xmlrpc.client.ServerProxy")
    def test_process_batch_missing_sequence_handling(self, mock_proxy_cls):
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 42
        mock_models = MagicMock()
        mock_models.execute_kw.return_value = []
        mock_proxy_cls.side_effect = lambda url: mock_common if "common" in url else mock_models

        client = OdooClient()
        payloads = [
            ("", {"ticket_sequence": "", "amount": 10}),
            ("none", {"ticket_sequence": None, "amount": 20}),
        ]

        results = client.process_batch(payloads, post=False, pay=False, pacing_delay=0)
        self.assertEqual(len(results["failed"]), 2)
        self.assertEqual(results["created"], [])
        self.assertEqual(results["skipped_duplicate"], [])


if __name__ == "__main__":
    unittest.main()
