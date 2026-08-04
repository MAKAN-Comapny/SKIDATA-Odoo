# SKIDATA-Odoo - GitHub Setup Script
# Creates a separate GitHub repo, pushes code, and configures Actions secrets.
# Prerequisites: Git and GitHub CLI (gh) installed and authenticated.

param(
    [string]$RepoName = "SKIDATA-Odoo",
    [switch]$Private = $false
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "=== SKIDATA-Odoo - GitHub Setup ===" -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Git is not installed or not in PATH." -ForegroundColor Red
    exit 1
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: GitHub CLI (gh) is not installed or not in PATH." -ForegroundColor Red
    exit 1
}

$authStatus = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: GitHub CLI is not authenticated. Run: gh auth login" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".git")) {
    Write-Host "Initializing git repository..." -ForegroundColor Green
    git init
}

Write-Host "Staging files..." -ForegroundColor Green
git add -A
git branch -M main 2>$null
$status = git status --porcelain
if ($status) {
    git commit -m "Initial commit: SKIDATA-Odoo parking pipeline with GitHub Actions"
    Write-Host "Committed changes." -ForegroundColor Green
} else {
    Write-Host "Nothing to commit (all clean)." -ForegroundColor Yellow
}

$remoteUrl = git config --get remote.origin.url 2>$null
if (-not $remoteUrl) {
    Write-Host "Creating GitHub repository '$RepoName'..." -ForegroundColor Green
    $visibility = if ($Private) { "--private" } else { "--public" }
    gh repo create $RepoName $visibility --source=. --remote=origin --push
    Write-Host "Repository created and pushed." -ForegroundColor Green
} else {
    Write-Host "Remote 'origin' already exists. Pushing..." -ForegroundColor Green
    git push -u origin HEAD
}

$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
    Write-Host ""
    Write-Host "Setting GitHub Actions secrets from .env..." -ForegroundColor Green
    $envContent = Get-Content $envFile
    $secretNames = @(
        "SKIDATA_URL", "SKIDATA_TENANT", "SKIDATA_LOGIN", "SKIDATA_PASSWORD",
        "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET",
        "SHAREPOINT_SITE_NAME", "TARGET_FOLDER_PATH"
    )
    foreach ($line in $envContent) {
        if ($line -match "^\s*#") { continue }
        if ($line -match "^([^=]+)=(.*)$") {
            $name = $Matches[1].Trim()
            $value = $Matches[2].Trim()
            if ($secretNames -contains $name -and $value) {
                $value | gh secret set $name
                Write-Host "  Set: $name" -ForegroundColor Gray
            }
        }
    }
    # Ensure SharePoint folder secret is correct even if .env had another value
    "SKIDATA - Odoo" | gh secret set TARGET_FOLDER_PATH
    Write-Host "  Set: TARGET_FOLDER_PATH (SKIDATA - Odoo)" -ForegroundColor Gray
    Write-Host "Secrets configured." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "WARNING: .env not found. Add secrets manually in GitHub Settings -> Secrets." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host "Workflow runs daily at 03:00 UTC, or manually from the Actions tab."
Write-Host "Repo: https://github.com/$(gh api user --jq .login)/$RepoName"
