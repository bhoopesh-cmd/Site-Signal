# Monitoring Report

- Run ID: b86777c1
- Run timestamp: 2026-10-30T09:39:35.207866+00:00
- Label: monitor
- Dry run: True

## Metrics

- Resources checked: 5
- Requests issued: 5
- Total bytes transferred: 5539637
- Total elapsed: 3430ms
- Confidently unchanged: 1
- Potential changes: 1
- Extraction jobs triggered: 1
- QA jobs triggered: 1
- Failures: 1
- Unknown states: 1
- Fallback executions: 1

## Decisions

- unknown: 1
- would_skip: 3
- would_trigger_extraction: 1

## Strategy distribution

- A: 2
- B: 2
- E: 1

## Notes

- Conditional HTTP used only for verified subset (Strategy A)
- Unverified validators treated as candidates, not trusted
- Failed requests never classified as unchanged
- Browser fallback (Strategy D) reported but not executed
- Reconciliation available via --reconcile flag
