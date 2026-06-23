# P6 — Happy-path E2E demo (Windows PowerShell)
$ErrorActionPreference = "Stop"
$REPORT = if ($env:REPORT) { $env:REPORT } else { "./demo-report.json" }
$CRITERIA = if ($env:CRITERIA) { $env:CRITERIA } else { "./examples/demo-criteria.yaml" }

Write-Host "=== P6 E2E Happy Path ==="
Write-Host "REPORT=$REPORT  CRITERIA=$CRITERIA"

try { chaosgen analyze --check } catch { }

Write-Host "[1/6] Analyze + generate..."
chaosgen analyze --hours 24 --generate --top-n 3 --save-report $REPORT

Write-Host "[2/6] Incidents from report..."
try { chaosgen incidents --from-report $REPORT --state described --output table } catch { }
chaosgen incidents --from-report $REPORT --output table

Write-Host "[3/6] HITL dry-run..."
try { chaosgen run --dry-run } catch { }

Write-Host "[4/6] Promote..."
try {
    chaosgen promote --from-report $REPORT --incident-id 0 --experiment 0 `
        --approved-by demo --criteria-file $CRITERIA
} catch {
    Write-Host "Promote skipped (no DESCRIBED incident) — OK for TRANSIENT-only runs."
}

Write-Host "[5/6] Catalog..."
chaosgen generate --from-catalog --arch microservices

Write-Host "[6/6] Evaluate..."
try { chaosgen evaluate --ab } catch { chaosgen evaluate }

Write-Host "=== P6 Happy Path complete ==="
