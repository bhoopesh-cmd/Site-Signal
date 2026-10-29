# Monitoring Report

- Run ID: 23b9dfe1
- Run timestamp: 2026-10-29T23:57:43.236852+00:00
- Label: e2e
- Dry run: True

## Metrics

- Resources checked: 5
- Requests issued: 5
- Total bytes transferred: 5539637
- Total elapsed: 6458ms
- Confidently unchanged: 0
- Potential changes: 0
- Extraction jobs triggered: 0
- QA jobs triggered: 0
- Failures: 1
- Unknown states: 3
- Fallback executions: 3

## Decisions

- unknown: 3
- would_skip: 2

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
