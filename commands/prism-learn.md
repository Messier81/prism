---
description: Update patterns from recent PRs. Re-scrapes recent history, combines with existing comments, and re-clusters semantically. Run periodically to keep patterns fresh.
argument-hint: "[--weeks 2] [--repo owner/repo]"
---

# Prism Learn

You are updating this repo's review patterns with recent PR data.

**Arguments:** $ARGUMENTS

---

## Pre-check

Verify `.prism/patterns.json` exists. If not, tell the user to run `/prism-init` first.

## Step 1: Determine Scope

Default to last 2 weeks. If `--weeks` is provided, use that value.

Detect repo from `--repo` argument or `git remote get-url origin`.

## Step 2: Collect Implicit Signals

Before scraping new data, collect post-review behavioral signals — did PR authors fix what Prism flagged?

```bash
python3 .prism/scripts/scrape.py track --repo <REPO> --prism-dir .prism
```

This checks commits on previously reviewed PRs, detects whether flagged files were changed after the review, and appends implicit feedback to `.prism/feedback.jsonl`. It also detects reverts of reviewed PRs and boosts confidence for patterns that fired on them.

## Step 3: Run Incremental Scrape

Run the scraper with a short time window:

```bash
python3 .prism/scripts/scrape.py --repo <REPO> --months 1 --output .prism/history/incremental
```

## Step 4: Merge Comment Pools

Read both comment sets:
- `.prism/history/comments.json` (existing comments from the original init)
- `.prism/history/incremental/history/comments.json` (new comments from recent PRs)

Deduplicate by matching on `body` + `path` + `created_at`. Combine into a single list.

Also incorporate feedback from `.prism/feedback.jsonl`:
- For each feedback entry where `action` is `"confirm"`, find the matching comment (by pattern_id → pattern → examples) and ensure `acted_on` is `true`
- For each `"dismiss"`, ensure `acted_on` is `false`

Write the merged comment pool back to `.prism/history/comments.json`.

## Step 5: Re-cluster Semantically

With the combined comment pool, perform semantic clustering the same way `/prism-init` Step 3 does:

1. Cluster all comments into 5-20 semantic themes
2. For each cluster, produce: name, question, description
3. Calculate action rates from `acted_on` fields
4. Build file triggers from paths

**Preserve pattern IDs where possible**: if a new cluster matches an existing pattern by name (or high overlap in comments), keep the existing PAT-XXX id so calibration history remains connected.

Assign new PAT-XXX ids only for genuinely new themes.

## Step 6: Re-synthesize Edges

With the updated patterns, re-synthesize relationships:
- Keep edges that still apply (both source and target patterns still exist)
- Add new edges for new patterns
- Remove edges where a pattern was dropped

Write to `.prism/edges.json`.

## Step 7: Write Updated Files

- `.prism/patterns.json` — updated semantic patterns
- `.prism/edges.json` — updated relationships
- `.prism/PATTERNS.md` — regenerated with new data
- `.prism/summary.json` — updated stats with `last_updated` timestamp

Re-run calibration with the updated patterns:
```bash
python3 .prism/scripts/scrape.py calibrate --prism-dir .prism --risk-level MEDIUM
```

Clean up `.prism/history/incremental/`.

## Step 8: Report

Tell the user:
- How many implicit feedback signals were collected (from `track`)
- How many new PRs and comments were added to the pool
- Which patterns changed significantly (>10% action rate shift or suppression status changed)
- Any new patterns discovered
- Any patterns dropped (no longer enough comments)
- Edge changes
- Suggest committing the updated files (including `feedback.jsonl` and `calibration.json`)
