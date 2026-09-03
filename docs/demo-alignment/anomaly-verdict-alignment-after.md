# Anomaly ↔ Verdict Alignment Report

Generated: 2026-08-29T03:57:47.387979+00:00
Model: `H:\Project\AIO-Chaos-Tool\models\demo_retrained.joblib`
Runs directory: `H:\Project\AIO-Chaos-Tool\scratch\telemetry\runs`

## Thesis narrative

Isolation Forest was trained on healthy baseline telemetry (unsupervised). Per-run chaos windows were scored without leaking verdict labels into fit(). On 3 evaluable runs, alignment rate was 67% (FAIL detection 100%, PASS specificity 0%).

## Summary metrics

| Metric | Value |
| --- | --- |
| Evaluable runs | 3 |
| Excluded runs | 0 |
| Aligned runs | 2 |
| Alignment rate | 66.67% |
| FAIL runs | 2 |
| FAIL with IF spike | 2 |
| FAIL detection rate | 100.00% |
| PASS runs | 1 |
| PASS without spike | 0 |
| PASS specificity | 0.00% |
| PARTIAL runs | 0 |
| PARTIAL with spike | 0 |

## Confusion-style matrix (Verdict × IF spike)

| Verdict | IF spike | Count | Interpretation |
| --- | --- | --- | --- |
| FAIL | yes | 2 | correct detection |
| PASS | yes | 1 | false alarm |

## Per-run detail

| Run | Verdict | IF spike | anomaly_fraction | max_severity | Outcome |
| --- | --- | --- | --- | --- | --- |
| multi-kill | PASS | yes | 100.00% | high | AlignmentOutcome.FALSE_ALARM |
| latency test | FAIL | yes | 100.00% | high | AlignmentOutcome.ALIGNED |
| partial failure | FAIL | yes | 100.00% | high | AlignmentOutcome.ALIGNED |

## Notes

- IF remains unsupervised; verdict labels are validation only.
- PARTIAL + spike counts as weak positive alignment.
- Aborted/dry-run/empty telemetry runs are excluded.
