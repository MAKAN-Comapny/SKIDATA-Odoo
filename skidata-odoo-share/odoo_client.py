"""
Odoo XML-RPC client wrapper.

Key robustness behaviors:
  - Dedupe: before creating, checks whether an invoice with this
    ticket_sequence already exists, so re-running the same export
    (or an overlapping day) never creates duplicates.
  - Per-row error isolation: one bad ticket's failure doesn't stop
    the rest of the batch.
  - Draft by default: process_batch only creates draft invoices unless
    post=True is passed. Registering a payment (pay=True) implies
    post=True, since Odoo requires an invoice to be posted before a
    payment can be registered against it.
  - Transient error retry with exponential backoff: handles 502/503/504
    HTML gateway timeout and proxy disconnects gracefully.
  - Micro-pacing between requests: prevents saturating Odoo web workers.
"""

import http.client
import socket
import time
import xmlrpc.client

import config


def is_transient_rpc_error(exc: Exception) -> bool:
    """Detect transient gateway, proxy (502/503/504), network, or server overload errors."""
    msg = str(exc).lower()
    if "<!doctype html" in msg or "<html" in msg:
        return True
    if any(
        code in msg
        for code in (
            "502",
            "503",
            "504",
            "bad gateway",
            "gateway time-out",
            "service temporarily unavailable",
        )
    ):
        return True
    if any(
        net_err in msg
        for net_err in (
            "connection reset",
            "connection refused",
            "remotedisconnected",
            "timed out",
            "timeout",
            "broken pipe",
        )
    ):
        return True
    if isinstance(
        exc,
        (
            xmlrpc.client.ProtocolError,
            xmlrpc.client.ResponseError,
            ConnectionError,
            TimeoutError,
            socket.timeout,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
            OSError,
        ),
    ):
        return True
    return False


class OdooClient:
    def __init__(self):
        self.url = config.ODOO_URL
        self.db = config.ODOO_DB
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(
            self.db, config.ODOO_USERNAME, config.ODOO_API_KEY, {}
        )
        if not self.uid:
            raise RuntimeError(
                "Odoo authentication failed -- check ODOO_USERNAME/ODOO_API_KEY"
            )
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

    def _execute(self, model, method, *args, max_retries=4, base_delay=2.0, **kwargs):
        """Execute an XML-RPC method on Odoo with exponential backoff for transient errors."""
        last_exc = None
        for attempt in range(max_retries):
            try:
                return self.models.execute_kw(
                    self.db,
                    self.uid,
                    config.ODOO_API_KEY,
                    model,
                    method,
                    list(args),
                    kwargs,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries - 1 and is_transient_rpc_error(exc):
                    delay = base_delay * (2**attempt)
                    print(
                        f"Warning: Transient Odoo RPC error during {model}.{method} "
                        f"(attempt {attempt + 1}/{max_retries}): {exc!r}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    try:
                        self.models = xmlrpc.client.ServerProxy(
                            f"{self.url}/xmlrpc/2/object"
                        )
                    except Exception:
                        pass
                else:
                    raise last_exc

    def already_posted(self, ticket_sequence):
        """Dedupe check: does an account.move with this ticket_sequence exist already?"""
        ids = self._execute(
            "account.move",
            "search",
            [("ticket_sequence", "=", ticket_sequence)],
            limit=1,
        )
        return bool(ids)

    def create_invoice(self, payload):
        """Create a single draft invoice. Returns the new move id."""
        return self._execute("account.move", "create", payload)

    def action_post(self, move_id):
        """Validate (post) a draft invoice."""
        return self._execute("account.move", "action_post", [move_id])

    def register_payment(self, move_id, journal_id=None):
        """
        Register a full payment against a posted invoice via the standard
        account.payment.register wizard.
        """
        ctx = {"active_model": "account.move", "active_ids": [move_id]}
        vals = {"journal_id": journal_id} if journal_id else {}
        wizard_id = self._execute(
            "account.payment.register", "create", vals, context=ctx
        )
        return self._execute(
            "account.payment.register", "action_create_payments", [wizard_id]
        )

    def process_batch(
        self,
        ticket_payloads,
        post=False,
        pay=False,
        payment_journal_id=None,
        pacing_delay=0.08,
    ):
        """
        ticket_payloads: list of (access_id_or_ticket_sequence, payload) tuples.
        post: validate (action_post) each created invoice.
        pay: register a full payment for each invoice (implies post=True).
        pacing_delay: sleep in seconds between tickets to protect Odoo web workers (default: 80ms).
        """
        if pay:
            post = True

        results = {"created": [], "skipped_duplicate": [], "failed": []}

        for key, payload in ticket_payloads:
            ticket_sequence = payload["ticket_sequence"]
            try:
                if self.already_posted(ticket_sequence):
                    results["skipped_duplicate"].append(ticket_sequence)
                    continue

                move_id = self.create_invoice(payload)
                entry = {
                    "ticket_sequence": ticket_sequence,
                    "move_id": move_id,
                    "posted": False,
                    "paid": False,
                }

                if post:
                    try:
                        self.action_post(move_id)
                        entry["posted"] = True
                    except Exception as exc:  # noqa: BLE001
                        entry["post_error"] = str(exc)

                if pay and entry["posted"]:
                    try:
                        self.register_payment(move_id, journal_id=payment_journal_id)
                        entry["paid"] = True
                    except Exception as exc:  # noqa: BLE001
                        entry["pay_error"] = str(exc)

                results["created"].append(entry)

            except Exception as exc:  # noqa: BLE001
                results["failed"].append(
                    {"ticket_sequence": ticket_sequence, "error": str(exc)}
                )

            if pacing_delay > 0:
                time.sleep(pacing_delay)

        return results
