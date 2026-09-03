# Anomaly ↔ Verdict Alignment Report

Generated: 2026-08-29T04:39:21.702819+00:00
Model: `models\merged_canonical_before_live.joblib`
Runs directory: `H:\Project\AIO-Chaos-Tool\scratch\telemetry\runs`

## Thesis narrative

Isolation Forest was trained on healthy baseline telemetry (unsupervised). Per-run chaos windows were scored without leaking verdict labels into fit(). On 1 evaluable runs, alignment rate was 0% (FAIL detection 0%, PASS specificity 0%).

## Summary metrics

| Metric | Value |
| --- | --- |
| Evaluable runs | 1 |
| Excluded runs | 0 |
| Aligned runs | 0 |
| Alignment rate | 0.00% |
| FAIL runs | 0 |
| FAIL with IF spike | 0 |
| FAIL detection rate | 0.00% |
| PASS runs | 1 |
| PASS without spike | 0 |
| PASS specificity | 0.00% |
| PARTIAL runs | 0 |
| PARTIAL with spike | 0 |

## Confusion-style matrix (Verdict × IF spike)

| Verdict | IF spike | Count | Interpretation |
| --- | --- | --- | --- |
| PASS | yes | 1 | false alarm |

## Per-run detail

| Run | Verdict | IF spike | anomaly_fraction | max_severity | Outcome |
| --- | --- | --- | --- | --- | --- |
| flagship-multi-kill | PASS | yes | 100.00% | high | AlignmentOutcome.FALSE_ALARM |

## Notes

- IF remains unsupervised; verdict labels are validation only.
- PARTIAL + spike counts as weak positive alignment.
- Aborted/dry-run/empty telemetry runs are excluded.
