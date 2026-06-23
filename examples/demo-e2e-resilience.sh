#!/usr/bin/env bash
# P6 — Resilience beat: LLM down → describe fallback → zero scenarios → promote blocked
set -euo pipefail

REPORT="${REPORT:-./demo-report-resilience.json}"
CRITERIA="${CRITERIA:-./examples/demo-criteria.yaml}"

echo "=== P6 Resilience Beat ==="
echo "Stop Ollama (or set invalid OLLAMA_URL) before step 2."
read -r -p "Press Enter when LLM path is disabled..."

echo "[1/4] Generate with LLM unavailable..."
chaosgen generate --save-report "$REPORT" --show-transient || true

echo "[2/4] Inspect report (expect describe_fallback or empty descriptions)..."
chaosgen incidents --from-report "$REPORT" --output table || true

echo "[3/4] Promote must fail on fallback..."
if chaosgen promote \
  --from-report "$REPORT" \
  --incident-id 0 \
  --experiment 0 \
  --approved-by demo \
  --criteria-file "$CRITERIA" 2>/dev/null; then
  echo "ERROR: promote should have been rejected" >&2
  exit 1
else
  echo "Promote correctly rejected."
fi

echo "[4/4] Catalog recovery (no LLM)..."
chaosgen generate --from-catalog --arch microservices
chaosgen run --dry-run || true

echo "=== P6 Resilience Beat complete ==="
