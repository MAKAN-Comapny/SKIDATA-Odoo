"""
Odoo XML-RPC client wrapper.

Key robustness behaviors:
  - Fast Batch Dedupe: checks existing ticket_sequences in bulk chunks (e.g. 1000/req)
    before processing, allowing 40,000 tickets to be evaluated in ~5 seconds.
  - Intra-batch dedupe: prevents duplicate creation if the input file contains identical tickets.
  - Per-row error isolation: one bad ticket's failure doesn't stop the rest of the batch.
  - Draft by default: process_batch only creates draft invoices unless
    post=True is passed. Registering a payment (pay=True) implies
    post=True, since Odoo requires an invoice to be posted before a
    payment can be registered against it.
  - Transient error retry with exponential backoff: handles 502/503/504
    HTML gateway timeout, proxy disconnects, and connection drops gracefully.
  - Micro-pacing between creates: protects Odoo web workers only when creating invoices.
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
        for attempt in range(1, 4):
            try:
                self.uid = common.authenticate(
                    self.db, config.ODOO_USERNAME, config.ODOO_API_KEY, {}
                )
                break
            except Exception as exc:
                if attempt < 3 and is_transient_rpc_error(exc):
                    time.sleep(2 * attempt)
                    common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
                    continue
                raise
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

    def get_existing_ticket_sequences(
        self, ticket_sequences, chunk_size=1000, only_posted=True, require_paid=False
    ):
        """
        Batch dedupe check: returns a set of ticket_sequences that already exist in Odoo.
        By default, only checks for 'posted' invoices so interrupted/draft invoices are not falsely skipped.
        If require_paid=True, only considers invoices that are both posted and paid.
        Queries Odoo in chunks of chunk_size using 'in' operator to stay well within parameter limits.
        """
        existing = set()
        clean_sequences = [
            str(s).strip() for s in ticket_sequences if s and str(s).strip()
        ]
        unique_sequences = list(dict.fromkeys(clean_sequences))
        if not unique_sequences:
            return existing

        total_chunks = (len(unique_sequences) + chunk_size - 1) // chunk_size
        print(
            f"Batch checking {len(unique_sequences)} tickets against Odoo in {total_chunks} chunk(s)..."
        )

        domain_base = []
        if only_posted:
            domain_base.append(("state", "=", "posted"))
        if require_paid:
            domain_base.append(("payment_state", "=", "paid"))

        for idx in range(0, len(unique_sequences), chunk_size):
            chunk = unique_sequences[idx : idx + chunk_size]
            chunk_num = (idx // chunk_size) + 1
            domain = [("ticket_sequence", "in", chunk)] + domain_base
            try:
                records = self._execute(
                    "account.move",
                    "search_read",
                    domain,
                    ["ticket_sequence"],
                )
                for r in records:
                    seq = r.get("ticket_sequence")
                    if seq and isinstance(seq, str):
                        existing.add(seq.strip())
            except Exception as exc:
                print(
                    f"Error during batch dedupe check (chunk {chunk_num}/{total_chunks}): {exc!r}"
                )
                raise

        print(
            f"Batch dedupe check complete: found {len(existing)} existing tickets in Odoo."
        )
        return existing

    def get_existing_records_map(self, ticket_sequences, chunk_size=1000):
        """
        Batch query existing invoices in Odoo by ticket_sequence.
        Returns a dict: ticket_sequence -> {'id': move_id, 'state': state, 'payment_state': payment_state}
        In case of multiple records for the same ticket, prioritizes posted and paid records.
        """
        records_map = {}
        clean_sequences = [
            str(s).strip() for s in ticket_sequences if s and str(s).strip()
        ]
        unique_sequences = list(dict.fromkeys(clean_sequences))
        if not unique_sequences:
            return records_map

        total_chunks = (len(unique_sequences) + chunk_size - 1) // chunk_size
        fields = ["id", "ticket_sequence", "state", "payment_state"]

        for idx in range(0, len(unique_sequences), chunk_size):
            chunk = unique_sequences[idx : idx + chunk_size]
            chunk_num = (idx // chunk_size) + 1
            domain = [("ticket_sequence", "in", chunk)]
            try:
                records = self._execute(
                    "account.move",
                    "search_read",
                    domain,
                    fields,
                )
                for r in records:
                    seq = str(r.get("ticket_sequence") or "").strip()
                    if not seq:
                        continue
                    existing_entry = records_map.get(seq)
                    if not existing_entry:
                        records_map[seq] = r
                    else:
                        # Prioritize posted over draft/cancelled
                        if r.get("state") == "posted" and existing_entry.get("state") != "posted":
                            records_map[seq] = r
                        # Prioritize paid over not_paid
                        elif r.get("payment_state") == "paid" and existing_entry.get("payment_state") != "paid":
                            records_map[seq] = r
            except Exception as exc:
                print(
                    f"Error during batch records query (chunk {chunk_num}/{total_chunks}): {exc!r}"
                )
                raise

        return records_map

    def already_posted(self, ticket_sequence):
        """Dedupe check: does an account.move with this ticket_sequence exist already in posted state?"""
        ids = self._execute(
            "account.move",
            "search",
            [("ticket_sequence", "=", ticket_sequence), ("state", "=", "posted")],
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
        existing_sequences_set=None,
    ):
        """
        ticket_payloads: list of (access_id_or_ticket_sequence, payload) tuples.
        post: validate (action_post) each created invoice.
        pay: register a full payment for each invoice (implies post=True).
        pacing_delay: sleep in seconds between CREATIONS to protect Odoo web workers (default: 80ms).
        existing_sequences_set: optional set of ticket_sequences known to exist already in Odoo.
                                If None, auto-fetches using get_existing_ticket_sequences().
        """
        if pay:
            post = True

        results = {"created": [], "skipped_duplicate": [], "failed": []}

        # 1. Fast Batch Query for all ticket sequences if not provided
        existing_records_map = {}
        if existing_sequences_set is None:
            all_seqs = [
                payload.get("ticket_sequence")
                for _, payload in ticket_payloads
                if isinstance(payload, dict) and payload.get("ticket_sequence")
            ]
            existing_records_map = self.get_existing_records_map(all_seqs)
            existing_sequences_set = set(existing_records_map.keys())
        else:
            existing_sequences_set = set(existing_sequences_set)

        # 2. Process payloads
        for key, payload in ticket_payloads:
            ticket_sequence = str(payload.get("ticket_sequence") or "").strip()
            if not ticket_sequence:
                results["failed"].append(
                    {"ticket_sequence": "", "error": "Missing ticket_sequence in payload"}
                )
                continue

            try:
                # Deduplication & Auto-Heal for Existing Records
                if ticket_sequence in existing_sequences_set:
                    rec = existing_records_map.get(ticket_sequence)
                    if rec:
                        move_id = rec.get("id")
                        move_state = rec.get("state")
                        pay_state = rec.get("payment_state")

                        # Case A: Invoice is posted but payment dropped/missing -> Auto-heal payment!
                        if pay and move_state == "posted" and pay_state != "paid":
                            try:
                                self.register_payment(move_id, journal_id=payment_journal_id)
                                entry = {
                                    "ticket_sequence": ticket_sequence,
                                    "move_id": move_id,
                                    "posted": True,
                                    "paid": True,
                                    "healed": True,
                                }
                                results["created"].append(entry)
                                rec["payment_state"] = "paid"
                                continue
                            except Exception as pay_exc:
                                results["failed"].append(
                                    {
                                        "ticket_sequence": ticket_sequence,
                                        "error": f"Failed to register payment on existing posted move {move_id}: {pay_exc}",
                                    }
                                )
                                continue

                        # Case B: Invoice exists in draft -> Auto-heal post and pay!
                        elif move_state == "draft":
                            try:
                                if post:
                                    self.action_post(move_id)
                                    rec["state"] = "posted"
                                if pay:
                                    self.register_payment(move_id, journal_id=payment_journal_id)
                                    rec["payment_state"] = "paid"
                                entry = {
                                    "ticket_sequence": ticket_sequence,
                                    "move_id": move_id,
                                    "posted": (rec.get("state") == "posted"),
                                    "paid": (rec.get("payment_state") == "paid"),
                                    "healed": True,
                                }
                                results["created"].append(entry)
                                continue
                            except Exception as draft_exc:
                                results["failed"].append(
                                    {
                                        "ticket_sequence": ticket_sequence,
                                        "error": f"Failed to post/pay draft move {move_id}: {draft_exc}",
                                    }
                                )
                                continue

                    # Case C: Fully posted & paid (or external duplicate set provided) -> Skip duplicate cleanly
                    results["skipped_duplicate"].append(ticket_sequence)
                    continue

                try:
                    move_id = self.create_invoice(payload)
                except Exception as create_exc:
                    # If invoice was already created in draft, recover its existing move_id
                    draft_ids = self._execute(
                        "account.move",
                        "search",
                        [("ticket_sequence", "=", ticket_sequence)],
                        limit=1,
                    )
                    if draft_ids:
                        move_id = draft_ids[0]
                    else:
                        raise create_exc

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

                # Track locally to protect against intra-file duplicates
                existing_sequences_set.add(ticket_sequence)
                results["created"].append(entry)

                # Micro-pacing applied ONLY when creating invoices, not when skipping duplicates
                if pacing_delay > 0:
                    time.sleep(pacing_delay)

            except Exception as exc:  # noqa: BLE001
                results["failed"].append(
                    {"ticket_sequence": ticket_sequence, "error": str(exc)}
                )

        return results
