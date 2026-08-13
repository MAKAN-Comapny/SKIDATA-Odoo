"""
Odoo XML-RPC client wrapper.

Key robustness behaviors:
  - Dedupe: before creating, checks whether an invoice with this
    ticket_sequence already exists, so re-running the same export
    (or an overlapping day) never creates duplicates.
  - Per-row error isolation: one bad ticket's failure doesn't stop
    the rest of the batch.
  - Never calls action_post -- records are created in Odoo's default
    draft state, left for a human to review and validate.
"""

import xmlrpc.client
import config


class OdooClient:
    def __init__(self):
        self.url = config.ODOO_URL
        self.db = config.ODOO_DB
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(self.db, config.ODOO_USERNAME, config.ODOO_API_KEY, {})
        if not self.uid:
            raise RuntimeError("Odoo authentication failed -- check ODOO_USERNAME/ODOO_API_KEY")
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

    def _execute(self, model, method, *args, **kwargs):
        return self.models.execute_kw(
            self.db, self.uid, config.ODOO_API_KEY, model, method, list(args), kwargs
        )

    def already_posted(self, ticket_sequence):
        """Dedupe check: does an account.move with this ticket_sequence exist already?"""
        ids = self._execute(
            "account.move", "search",
            [("ticket_sequence", "=", ticket_sequence)],
            limit=1,
        )
        return bool(ids)

    def create_invoice(self, payload):
        """Create a single draft invoice. Returns the new move id."""
        return self._execute("account.move", "create", payload)

    def process_batch(self, ticket_payloads):
        """
        ticket_payloads: list of (access_id_or_ticket_sequence, payload) tuples.
        Returns a summary dict: created / skipped_duplicate / failed (with reasons).
        """
        results = {"created": [], "skipped_duplicate": [], "failed": []}

        for key, payload in ticket_payloads:
            ticket_sequence = payload["ticket_sequence"]
            try:
                if self.already_posted(ticket_sequence):
                    results["skipped_duplicate"].append(ticket_sequence)
                    continue

                move_id = self.create_invoice(payload)
                results["created"].append({"ticket_sequence": ticket_sequence, "move_id": move_id})

            except Exception as exc:  # noqa: BLE001 -- intentionally broad, isolate per-row
                results["failed"].append({"ticket_sequence": ticket_sequence, "error": str(exc)})

        return results
