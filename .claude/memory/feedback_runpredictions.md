---
name: Feedback — Re-running predictions and validating changes
description: How to properly re-run, grade, and validate prediction engine changes
type: feedback
---

Always `cd /c/Users/user/Desktop/sporty/backend` before running management commands.

When testing engine changes:
1. `run_predictions --date YYYY-MM-DD` to regenerate picks (updates/creates DB records)
2. `update_results --date YYYY-MM-DD` to grade PENDING records against actual scores
3. `scorecard` or `scorecard --month YYYY-MM` to check accuracy

**Why:** Running from wrong directory causes `manage.py: No such file or directory` errors.

Do NOT delete DB records casually — they represent historical performance. Only delete:
- Stale below-threshold records (grade < threshold that should never have been saved)
- Duplicate records for the same fixture/date (keep highest-id / most recent)

**Deadlock DC experiment (attempted, reverted):** Tried replacing Over 0.5 with Double Chance for "balanced" games (xG gap < 0.10, blank_risk > 0.15). Reverted because the DC direction (1X vs X2) is random in balanced games — fixed some 0-0 losses but created new losses when the wrong team won. Over 0.5 threshold raise (to 84) was the better solution.
