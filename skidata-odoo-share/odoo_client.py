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
"""

import xmlrpc.client

import config


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

    def _execute(self, model, method, *args, **kwargs):
        return self.models.execute_kw(
            self.db, self.uid, config.ODOO_API_KEY, model, method, list(args), kwargs
        )

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

    def process_batch(self, ticket_payloads, post=False, pay=False, payment_journal_id=None):
        """
        ticket_payloads: list of (access_id_or_ticket_sequence, payload) tuples.
        post: validate (action_post) each created invoice.
        pay: register a full payment for each invoice (implies post=True).
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

        return results
