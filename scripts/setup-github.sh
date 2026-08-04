#!/usr/bin/env bash
# SKIDATA-Odoo - GitHub Setup Script
set -euo pipefail

REPO_NAME="${1:-SKIDATA-Odoo}"
PRIVATE_FLAG="${2:-}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== SKIDATA-Odoo - GitHub Setup ==="

command -v git >/dev/null || { echo "ERROR: git not found"; exit 1; }
command -v gh >/dev/null || { echo "ERROR: gh not found"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: run gh auth login"; exit 1; }

if [ ! -d .git ]; then
  echo "Initializing git repository..."
  git init
fi

echo "Staging files..."
git add -A
git branch -M main 2>/dev/null || true
if [ -n "$(git status --porcelain)" ]; then
  git commit -m "Initial commit: SKIDATA-Odoo parking pipeline with GitHub Actions"
  echo "Committed changes."
else
  echo "Nothing to commit (all clean)."
fi

if ! git config --get remote.origin.url >/dev/null 2>&1; then
  echo "Creating GitHub repository '$REPO_NAME'..."
  VISIBILITY="--public"
  if [ "$PRIVATE_FLAG" = "--private" ]; then
    VISIBILITY="--private"
  fi
  gh repo create "$REPO_NAME" $VISIBILITY --source=. --remote=origin --push
  echo "Repository created and pushed."
else
  echo "Remote 'origin' already exists. Pushing..."
  git push -u origin HEAD
fi

if [ -f .env ]; then
  echo ""
  echo "Setting GitHub Actions secrets from .env..."
  SECRET_NAMES=(
    SKIDATA_URL SKIDATA_TENANT SKIDATA_LOGIN SKIDATA_PASSWORD
    TENANT_ID CLIENT_ID CLIENT_SECRET
    SHAREPOINT_SITE_NAME TARGET_FOLDER_PATH
  )
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^([^=]+)=(.*)$ ]] || continue
    name="${BASH_REMATCH[1]// /}"
    value="${BASH_REMATCH[2]}"
    value="$(echo "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    for s in "${SECRET_NAMES[@]}"; do
      if [ "$name" = "$s" ] && [ -n "$value" ]; then
        printf '%s' "$value" | gh secret set "$name"
        echo "  Set: $name"
      fi
    done
  done < .env
  printf '%s' "SKIDATA - Odoo" | gh secret set TARGET_FOLDER_PATH
  echo "  Set: TARGET_FOLDER_PATH (SKIDATA - Odoo)"
  echo "Secrets configured."
else
  echo "WARNING: .env not found. Add secrets manually."
fi

OWNER="$(gh api user --jq .login)"
echo ""
echo "=== Done ==="
echo "Workflow runs daily at 03:00 UTC, or manually from the Actions tab."
echo "Repo: https://github.com/${OWNER}/${REPO_NAME}"
