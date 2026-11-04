# Task: Print the total cost in engineering status

## Repository
/var/home/user/Documents/agent-workflows

## Base branch
main

## Branch
feature/status-total-cost

## Goal
`python3 engineering/run.py status <name>` prints one more line at the end: `total cost $X.XXXX`, the sum of the cost of every Claude call in the build.

## Details
- `engineering/run.py`: `cmd_status` prints the line after the `total` row. Use the totals that `usage_totals` already adds up.
- `tests/test_engineering.py`: add `test_status_prints_total_cost`. It writes a build with two usage lines and checks the printed line.
- With no calls yet the line is `total cost $0.0000`.

## Done when
- `status` ends with `total cost $X.XXXX`, four decimals.
- The total is the sum of `cost_usd` over every `usage.jsonl` line of the build.
- The new test passes and every old test still passes.

## Check commands
- python3 -m unittest discover -s tests

## Constraints
- Python standard library only.
- Do not change `marketing/` or `tests/test_run.py`.
- No fallbacks.
