"""
Tests for Odoo XML-RPC Client Retry and Transient Error Handling.
Verifies that HTML 502/504 Bad Gateway, Connection Reset, and Timeout errors
are properly detected and retried with backoff.
"""
import http.client
import os
import socket
import sys
import unittest
from unittest.mock import MagicMock, patch
import xmlrpc.client

# Set mock env vars before importing config/odoo_client
os.environ.setdefault("ODOO_URL", "https://mock.odoo.local")
os.environ.setdefault("ODOO_DB", "mock_db")
os.environ.setdefault("ODOO_USERNAME", "mock_user")
os.environ.setdefault("ODOO_API_KEY", "mock_key")
os.environ.setdefault("ODOO_JOURNAL_ID", "1")
os.environ.setdefault("ODOO_SALES_ACCOUNT_ID", "2")
os.environ.setdefault("ODOO_TAX_ID", "3")
os.environ.setdefault("ODOO_WALKIN_PARTNER_ID", "4")

# Add skidata-odoo-share to sys.path
SHARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skidata-odoo-share"
)
if SHARE_DIR not in sys.path:
    sys.path.insert(0, SHARE_DIR)

from odoo_client import OdooClient, is_transient_rpc_error


class TestOdooClientRobustness(unittest.TestCase):
    def test_transient_error_detection(self):
        # 1. HTML gateway errors (e.g. from Cloudflare / Nginx 502/504)
        self.assertTrue(is_transient_rpc_error(Exception("<!doctype html>\n<html>...")))
        self.assertTrue(is_transient_rpc_error(Exception("502 Bad Gateway")))
        self.assertTrue(is_transient_rpc_error(Exception("504 Gateway Time-out")))
        self.assertTrue(is_transient_rpc_error(Exception("503 Service Temporarily Unavailable")))

        # 2. Network disconnects and timeouts
        self.assertTrue(is_transient_rpc_error(ConnectionResetError("Connection reset by peer")))
        self.assertTrue(is_transient_rpc_error(http.client.RemoteDisconnected("Remote end closed connection without response")))
        self.assertTrue(is_transient_rpc_error(socket.timeout("The read operation timed out")))
        self.assertTrue(is_transient_rpc_error(xmlrpc.client.ProtocolError("odoo.makan.sa", 502, "Bad Gateway", {})))

        # 3. Non-transient errors (business logic errors / validation faults)
        self.assertFalse(is_transient_rpc_error(xmlrpc.client.Fault(2, "Missing required field: account_id")))
        self.assertFalse(is_transient_rpc_error(ValueError("Invalid date format")))

    @patch("odoo_client.xmlrpc.client.ServerProxy")
    def test_execute_retries_transient_html_error_and_succeeds(self, mock_proxy_cls):
        # Mock authenticate
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 42

        # Mock models.execute_kw
        mock_models = MagicMock()
        # Fail twice with HTML 502 Bad Gateway, then succeed on 3rd attempt
        mock_models.execute_kw.side_effect = [
            Exception("<!doctype html>\n<html><head><title>502 Bad Gateway</title></head></html>"),
            Exception("<!doctype html>\n"),
            [12345],  # success
        ]

        def proxy_factory(url):
            if "common" in url:
                return mock_common
            return mock_models

        mock_proxy_cls.side_effect = proxy_factory

        client = OdooClient()
        result = client._execute("account.move", "search", [("ticket_sequence", "=", "123")], max_retries=4, base_delay=0.01)

        self.assertEqual(result, [12345])
        self.assertEqual(mock_models.execute_kw.call_count, 3)

    @patch("odoo_client.xmlrpc.client.ServerProxy")
    def test_execute_raises_if_retries_exhausted(self, mock_proxy_cls):
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 42

        mock_models = MagicMock()
        # Continuously fail with 504
        mock_models.execute_kw.side_effect = Exception("504 Gateway Time-out")

        mock_proxy_cls.side_effect = lambda url: mock_common if "common" in url else mock_models

        client = OdooClient()
        with self.assertRaises(Exception) as ctx:
            client._execute("account.move", "search", [], max_retries=3, base_delay=0.01)

        self.assertIn("504 Gateway Time-out", str(ctx.exception))
        self.assertEqual(mock_models.execute_kw.call_count, 3)

    @patch("odoo_client.xmlrpc.client.ServerProxy")
    def test_execute_does_not_retry_non_transient_error(self, mock_proxy_cls):
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 42

        mock_models = MagicMock()
        mock_models.execute_kw.side_effect = xmlrpc.client.Fault(2, "Field does not exist")

        mock_proxy_cls.side_effect = lambda url: mock_common if "common" in url else mock_models

        client = OdooClient()
        with self.assertRaises(xmlrpc.client.Fault):
            client._execute("account.move", "search", [], max_retries=4, base_delay=0.01)

        # Fails immediately without burning useless retries on semantic faults
        self.assertEqual(mock_models.execute_kw.call_count, 1)


if __name__ == "__main__":
    unittest.main()
