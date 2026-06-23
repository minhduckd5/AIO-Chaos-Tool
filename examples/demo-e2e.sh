#!/usr/bin/env bash
# P6 — Happy-path E2E demo (thesis / lab)
# Requires: chaosgen installed, Prometheus/Loki or offline export, Ollama for LLM path.
set -euo pipefail

REPORT="${REPORT:-./demo-report.json}"
CRITERIA="${CRITERIA:-./examples/demo-criteria.yaml}"

echo "=== P6 E2E Happy Path ==="
echo "REPORT=$REPORT  CRITERIA=$CRITERIA"

chaosgen analyze --check || true

echo "[1/6] Analyze + generate (24h lookback)..."
chaosgen analyze --hours 24 --generate --top-n 3 --save-report "$REPORT"

echo "[2/6] Incidents from report snapshot..."
chaosgen incidents --from-report "$REPORT" --state described --output table || true
chaosgen incidents --from-report "$REPORT" --output table

echo "[3/6] HITL dry-run..."
chaosgen run --dry-run || true

echo "[4/6] Promote (P4 contract: --from-report + --criteria-file)..."
if chaosgen promote \
  --from-report "$REPORT" \
  --incident-id 0 \
  --experiment 0 \
  --approved-by demo \
  --criteria-file "$CRITERIA"; then
  echo "Promote succeeded."
else
  echo "Promote skipped or failed (no DESCRIBED incident / no experiment) — OK for TRANSIENT-only runs."
fi

echo "[5/6] Catalog regenerate..."
chaosgen generate --from-catalog --arch microservices

echo "[6/6] Evaluate..."
chaosgen evaluate --ab || chaosgen evaluate

echo "=== P6 Happy Path complete ==="
