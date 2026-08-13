# SKIDATA-Odoo

Scrapes **only** the Parking Transactions report from SKIDATA analytics, transforms it for Odoo, uploads to Microsoft SharePoint, then (second job) creates **draft** Customer Invoices in Odoo.

## Pipeline

1. **Scraper** – Logs into SKIDATA portal, downloads Parking Transactions (yesterday) to `exports/`
2. **Transformer** – Keeps rows with `Payment != 0`, remaps columns to the Odoo invoice structure
3. **SharePoint** – Uploads the transformed file to folder `SKIDATA - Odoo`
4. **Odoo import** (`skidata-odoo-share/`) – Downloads the latest SharePoint file and creates **draft** invoices (not posted)

## Local Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Copy `.env.example` to `.env` and fill in your credentials.

```bash
python main.py
```

Set `UPLOAD_TO_SHAREPOINT=false` in `.env` to skip upload while testing.

## GitHub Actions

Two workflows, both triggered manually or by **separate** cron-job.org jobs:

| Workflow | Event type | What it does |
|----------|------------|----------------|
| **SKIDATA-Odoo Parking Pipeline** | `manual-trigger` | Scrape → transform → SharePoint |
| **SKIDATA-Odoo SharePoint to Odoo Import** | `odoo-import` | Latest SharePoint file → Odoo **drafts** |

Use **two cron jobs** (import later than scrape, e.g. +30–60 min). Do not reuse `manual-trigger` for import.

### One-time setup

**Prerequisites:** [Git](https://git-scm.com/download/win), [GitHub CLI](https://cli.github.com/), then `gh auth login`.

```powershell
.\scripts\setup-github.ps1
```

Or from Git Bash:

```bash
bash scripts/setup-github.sh
```

This creates the GitHub repo, pushes the code, and sets secrets from `.env`.

### Required secrets

| Secret | Required |
|--------|----------|
| SKIDATA_URL | Yes |
| SKIDATA_TENANT | Yes |
| SKIDATA_LOGIN | Yes |
| SKIDATA_PASSWORD | Yes |
| TENANT_ID | Yes |
| CLIENT_ID | Yes |
| CLIENT_SECRET | Yes |
| SHAREPOINT_SITE_NAME | Yes |
| TARGET_FOLDER_PATH | Yes (`SKIDATA - Odoo`) |
| ODOO_URL | Yes (import job) |
| ODOO_DB | Yes (import job) |
| ODOO_USERNAME | Yes (import job) |
| ODOO_API_KEY | Yes (import job) |
| ODOO_JOURNAL_ID | Yes (import job) |
| ODOO_SALES_ACCOUNT_ID | Yes (import job) |
| ODOO_TAX_ID | Yes (import job) |
| ODOO_WALKIN_PARTNER_ID | Yes (import job) |

### External cron (cron-job.org)

**Job 1 — scraper** (`event_type`: `manual-trigger`):

```http
POST https://api.github.com/repos/MHamadaMk24/SKIDATA-Odoo/dispatches
Authorization: Bearer YOUR_GITHUB_PAT
Accept: application/vnd.github+json
Content-Type: application/json

{"event_type": "manual-trigger"}
```

**Job 2 — Odoo import** (`event_type`: `odoo-import`), later than job 1:

```http
POST https://api.github.com/repos/MHamadaMk24/SKIDATA-Odoo/dispatches
Authorization: Bearer YOUR_GITHUB_PAT
Accept: application/vnd.github+json
Content-Type: application/json

{"event_type": "odoo-import"}
```

## Environment

| Variable | Required | Notes |
|----------|----------|-------|
| SKIDATA_URL | Yes | SKIDATA analytics login URL |
| SKIDATA_TENANT | Yes | |
| SKIDATA_LOGIN | Yes | |
| SKIDATA_PASSWORD | Yes | |
| TENANT_ID | Yes | Azure AD tenant |
| CLIENT_ID | Yes | App registration |
| CLIENT_SECRET | Yes | App registration |
| SHAREPOINT_SITE_NAME | Yes | Site URL or display name |
| TARGET_FOLDER_PATH | No | Default: `SKIDATA - Odoo` |
| UPLOAD_TO_SHAREPOINT | No | Default: `true` |
| HEADLESS | No | `true` in CI; default `false` locally |
| ODOO_* | Yes for import | See secrets table |
| IMPORT_LIMIT | No | If set, import only N rows (testing) |

### Local Odoo import

```bash
cd skidata-odoo-share
pip install -r requirements.txt
python dry_run.py 5
python main_import.py
```
