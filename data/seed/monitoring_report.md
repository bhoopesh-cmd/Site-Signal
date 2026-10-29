# Monitoring Report

- Run ID: 7b12bb04
- Run timestamp: 2026-10-29T20:11:37.570640+00:00
- Label: monitor
- Dry run: True

## Metrics

- Resources checked: 117
- Requests issued: 117
- Total bytes transferred: 54271434
- Total elapsed: 265920ms
- Confidently unchanged: 0
- Potential changes: 0
- Extraction jobs triggered: 0
- QA jobs triggered: 0
- Failures: 38
- Unknown states: 112
- Fallback executions: 112

## Decisions

- unknown: 112
- would_skip: 5

## Strategy distribution

- A: 20
- B: 56
- C: 5
- D: 4
- E: 32

## Notes

- Conditional HTTP used only for verified subset (Strategy A)
- Unverified validators treated as candidates, not trusted
- Failed requests never classified as unchanged
- Browser fallback (Strategy D) reported but not executed
- Reconciliation available via --reconcile flag
