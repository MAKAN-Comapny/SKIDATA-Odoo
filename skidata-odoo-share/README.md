# Parking Ticket -> Odoo Import

Takes a SkiData parking-transactions export that's already shaped like an
Odoo import template (columns like `Invoice/Bill Date`, `In Time`,
`Out Time`, `Ticket Sequence`, `Invoice Lines/Analytics Distribution`) and
creates the corresponding **draft** Customer Invoices in Odoo, so a human
can review and post them.

## Files
- `import_csv_batch.py` — reads the export (`.csv` or `.xlsx`), builds one
  `account.move` payload per row, and uploads via `odoo_client.py`
- `odoo_client.py` — Odoo XML-RPC client: dedupe check (by `ticket_sequence`),
  per-row create, per-row error isolation, always leaves invoices in draft
- `config.py` — all settings via environment variables (no secrets hard-coded)

## Setup
```
pip install -r requirements.txt
export ODOO_URL=...
export ODOO_DB=...
export ODOO_USERNAME=...
export ODOO_API_KEY=...          # Odoo API key, not your login password
export ODOO_JOURNAL_ID=...
export ODOO_SALES_ACCOUNT_ID=...
export ODOO_TAX_ID=...
export ODOO_WALKIN_PARTNER_ID=...
```
**Never paste real credentials into a chat/AI tool or commit them to git.**
Export them directly in your own terminal.

## Usage
```
python3 import_csv_batch.py path/to/export.csv [limit]
python3 import_csv_batch.py path/to/export.xlsx [limit]
```
`limit` defaults to 10. Prompts for confirmation before creating anything,
then prints created / skipped-duplicate / failed ticket sequences.

## Still open (confirm before trusting a full run)
1. **Plate Number field** — the source file has plate numbers for some
   tickets, but the target field name on `account.move` isn't confirmed
   yet, so it isn't written.
2. **Payment Reference field** — same as above, present in the source but
   not yet mapped to an Odoo field.
3. **Partner** — the source file's `Partner` column ("SKIDATA") isn't used;
   invoices are created against the configured walk-in partner
   (`ODOO_WALKIN_PARTNER_ID`) instead. Confirm that's correct.
4. **Analytic distribution** — passed through as-is from the source file's
   `Invoice Lines/Analytics Distribution` column; confirm the account ids
   it contains are valid in your target Odoo database.

## Archive
`_archive/` holds an earlier version of this pipeline that parsed the *raw*
SkiData xlsx export (before field-mapping was moved upstream) and ran as a
scheduled Azure Function pulling from SharePoint. Kept for reference only —
not part of the current flow.
