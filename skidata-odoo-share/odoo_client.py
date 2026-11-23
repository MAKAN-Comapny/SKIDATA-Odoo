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

    def get_existing_ticket_sequences(self, ticket_sequences, chunk_size=1000):
        """
        Batch dedupe check: returns a set of ticket_sequences that already exist in Odoo.
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

        for idx in range(0, len(unique_sequences), chunk_size):
            chunk = unique_sequences[idx : idx + chunk_size]
            chunk_num = (idx // chunk_size) + 1
            try:
                records = self._execute(
                    "account.move",
                    "search_read",
                    [("ticket_sequence", "in", chunk)],
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
        if existing_sequences_set is None:
            all_seqs = [
                payload.get("ticket_sequence")
                for _, payload in ticket_payloads
                if isinstance(payload, dict) and payload.get("ticket_sequence")
            ]
            existing_sequences_set = self.get_existing_ticket_sequences(all_seqs)
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
                # Instant in-memory deduplication check (0.01ms)
                if ticket_sequence in existing_sequences_set:
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
