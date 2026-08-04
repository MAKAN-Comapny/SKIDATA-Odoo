# SKIDATA-Odoo

Scrapes **only** the Parking Transactions report from SKIDATA analytics, transforms it for Odoo, and uploads to Microsoft SharePoint.

## Pipeline

1. **Scraper** – Logs into SKIDATA portal, downloads Parking Transactions (yesterday) to `exports/`
2. **Transformer** – Keeps rows with `Payment != 0`, remaps columns to the Odoo invoice structure
3. **SharePoint** – Uploads the transformed file to folder `SKIDATA - Odoo`

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

## GitHub Actions (cron)

The workflow runs:

| Trigger | When |
|---------|------|
| **schedule** | Daily at **03:00 UTC** |
| **workflow_dispatch** | Manual from Actions tab |
| **repository_dispatch** | External cron (e.g. [cron-job.org](https://cron-job.org)) |

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

### External cron (optional)

If GitHub schedule is delayed, call from cron-job.org:

```http
POST https://api.github.com/repos/OWNER/SKIDATA-Odoo/dispatches
Authorization: Bearer YOUR_GITHUB_PAT
Accept: application/vnd.github+json
Content-Type: application/json

{"event_type": "manual-trigger"}
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
