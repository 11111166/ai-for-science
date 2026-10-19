#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
(cd services/api && uvicorn app.main:app --reload --port 8000) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT
npm run dev:web
