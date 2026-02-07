#!/usr/bin/env bash
# Run from repo root. Dependencies must be installed first (pip install -r backend/requirements.txt).
# Only starts uvicorn; no pip install in this script.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -d ".venv" ] && source .venv/bin/activate
export PYTHONPATH="$ROOT"
exec uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
