#!/usr/bin/env bash
# Smoke test Railway deployment (no secrets; URL is the public backend URL).
# Usage:
#   RAILWAY_URL=https://your-app.up.railway.app ./scripts/smoke_test_railway.sh
#   ./scripts/smoke_test_railway.sh https://your-app.up.railway.app
set -e
URL="${1:-${RAILWAY_URL:-}}"
if [ -z "$URL" ]; then
  echo "Set RAILWAY_URL to your Railway backend URL (e.g. https://your-app.up.railway.app)"
  exit 1
fi
URL="${URL%/}"
echo "Testing $URL"
echo "--- curl -i $URL/health ---"
if ! curl -s -i -m 15 "$URL/health"; then
  echo "curl failed"
  exit 1
fi
echo ""
CODE=$(curl -s -o /dev/null -w "%{http_code}" -m 15 "$URL/health" || echo "000")
if [ "$CODE" = "502" ]; then
  echo "Hint: 502 often means PORT mismatch. Set PORT=8000 in Railway Variables and redeploy; then verify /health."
fi
echo "--- smoke_test.py ---"
python3 "$(dirname "$0")/smoke_test.py" "$URL"
