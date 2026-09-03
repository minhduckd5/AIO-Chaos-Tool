# Anomaly ↔ Verdict Alignment Report

Generated: 2026-08-29T03:57:47.185718+00:00
Model: `H:\Project\AIO-Chaos-Tool\models\demo_baseline.joblib`
Runs directory: `H:\Project\AIO-Chaos-Tool\scratch\telemetry\runs`

## Thesis narrative

Isolation Forest was trained on healthy baseline telemetry (unsupervised). Per-run chaos windows were scored without leaking verdict labels into fit(). On 3 evaluable runs, alignment rate was 33% (FAIL detection 0%, PASS specificity 100%).

## Summary metrics

| Metric | Value |
| --- | --- |
| Evaluable runs | 3 |
| Excluded runs | 0 |
| Aligned runs | 1 |
| Alignment rate | 33.33% |
| FAIL runs | 2 |
| FAIL with IF spike | 0 |
| FAIL detection rate | 0.00% |
| PASS runs | 1 |
| PASS without spike | 1 |
| PASS specificity | 100.00% |
| PARTIAL runs | 0 |
| PARTIAL with spike | 0 |

## Confusion-style matrix (Verdict × IF spike)

| Verdict | IF spike | Count | Interpretation |
| --- | --- | --- | --- |
| FAIL | no | 2 | missed chaos signal |
| PASS | no | 1 | correct quiet window |

## Per-run detail

| Run | Verdict | IF spike | anomaly_fraction | max_severity | Outcome |
| --- | --- | --- | --- | --- | --- |
| multi-kill | PASS | no | 0.00% | low | AlignmentOutcome.ALIGNED |
| latency test | FAIL | no | 0.00% | low | AlignmentOutcome.MISSED_ANOMALY |
| partial failure | FAIL | no | 0.00% | low | AlignmentOutcome.MISSED_ANOMALY |

## Notes

- IF remains unsupervised; verdict labels are validation only.
- PARTIAL + spike counts as weak positive alignment.
- Aborted/dry-run/empty telemetry runs are excluded.
