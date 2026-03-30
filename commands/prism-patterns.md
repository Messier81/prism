---
description: Show learned review patterns with action rates. Add manual patterns, edit existing ones, or see what your team cares about most.
argument-hint: "[--add] [--edit PAT-XXX] [--category migration|api_views|...]"
---

# Prism Patterns

You are showing this team's learned review patterns.

**Arguments:** $ARGUMENTS

---

## Pre-check

Verify `.prism/patterns.json` exists. If not, tell the user to run `/prism-init` first.

## Default: Show Patterns

Read `.prism/patterns.json` and `.prism/summary.json`. Present a formatted overview:

```
PRISM PATTERNS — <repo>
Learned from <N> PRs · <N> human review comments · <N> months of history

HIGH CONFIDENCE (team acts on these >80%)
  PAT-001  migration_review         100% action rate  (12 comments)
  PAT-003  api_views_review          96% action rate  (23 comments)
  ...

MEDIUM CONFIDENCE (40-80%)
  PAT-005  frontend_components       63% action rate  (19 comments)
  ...

LOW / NOISE (<40% — suppressed during review)
  PAT-008  docs_review               12% action rate  (8 comments)
  ...

MANUAL (added by team, calibrating)
  PAT-010  auth_middleware_check      neutral          (0 reviews so far)
```

If `--category` is provided, filter to that category.

## --add: Add a Manual Pattern

Ask the user for:
1. **Name**: short identifier (e.g. `permission_classes_check`)
2. **Category**: what type of files this applies to
3. **Trigger**: file glob patterns that activate it (e.g. `**/views/**/*.py`)
4. **Question**: what to check when this pattern fires
5. **Severity**: `must_fix`, `concern`, or `nit`

Create a new pattern with:
- `source: "manual"`
- `stats.action_rate: 0.5` (neutral starting point)
- `stats.total_comments: 0`

Append to `.prism/patterns.json` with the next available PAT-XXX id.

Tell the user: "Pattern added. It will start calibrating as reviewers confirm or dismiss its findings."

## --edit PAT-XXX: Edit a Pattern

Read the specified pattern and show its current values. Let the user modify any field. Write the updated pattern back to `.prism/patterns.json`.

Update `.prism/PATTERNS.md` to reflect changes.
