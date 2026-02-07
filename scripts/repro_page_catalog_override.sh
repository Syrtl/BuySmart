#!/usr/bin/env bash
# Reproduce Page Catalog request exactly like extension payload shape.
# Usage:
#   ./scripts/repro_page_catalog_override.sh
#   ./scripts/repro_page_catalog_override.sh http://127.0.0.1:8000
#   ./scripts/repro_page_catalog_override.sh https://your-app.up.railway.app
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8000}"
BASE_URL="${BASE_URL%/}"
PAYLOAD_FILE="$(dirname "$0")/page_catalog_override_fixture.json"

if [[ ! -f "$PAYLOAD_FILE" ]]; then
  echo "Missing payload fixture: $PAYLOAD_FILE" >&2
  exit 1
fi

echo "POST $BASE_URL/assistant/recommend"
curl -sS -i \
  -H "Content-Type: application/json" \
  -X POST "$BASE_URL/assistant/recommend" \
  --data-binary "@$PAYLOAD_FILE"
echo
