---
description: Scrape this repo's PR review history and learn team patterns. Creates .prism/ with patterns.json, summary.json, and PATTERNS.md. Run once per repo, then use /prism-learn to update.
argument-hint: "[--repo owner/repo] [--months 6]"
---

# Prism Init

You are initializing Prism for this repository. Your goal is to scrape the team's PR review history, learn what they actually care about in code reviews, and build a pattern database.

**Arguments:** $ARGUMENTS

---

## Step 1: Detect Repository

If `--repo` was provided in arguments, use that. Otherwise, detect from git remote:

```bash
git remote get-url origin
```

Parse the `owner/repo` from the URL. Confirm with the user: "I'll scrape review history from **owner/repo**. How many months back? (default: 6)"

## Step 2: Run the Scraper

The scrape script is at `.prism/scripts/scrape.py`. Run it:

```bash
python3 .prism/scripts/scrape.py --repo <REPO> --months <MONTHS> --max-prs 100 --output .prism
```

Use `--max-prs 100` for the first run (takes ~5 minutes). You can increase later with `/prism-learn`.

This will:
1. Fetch merged PRs from the last N months
2. For each PR, fetch files changed, review comments, and commits
3. Detect which review comments were acted on (code changed after comment) vs dismissed
4. Detect reverted PRs and CI check status
5. Track zero-comment approvals (safe change types)
6. Write results to `.prism/`

**The script checkpoints every 10 PRs.** If it times out or crashes, re-run the same command and it resumes from where it left off.

## Step 3: Semantic Clustering

The scraper outputs raw data in `.prism/history/`. Read ALL of these:

- **`comments.json`** — all human review comments with `body`, `path`, `acted_on`
- **`reverts.json`** — reverted PRs: what files/categories were involved, whether the original had review comments
- **`safe_changes.json`** — change types that were approved without comments and never reverted
- **`reviews.json`** — full PR data including `ci_checks` per PR

Also read `.prism/summary.json` for `ci_checks_available` (what CI already catches).

Now cluster the comments into 5-20 semantic themes based on WHAT they are about (not where the files are). For each cluster, produce:

- `name`: short snake_case identifier (e.g. `input_validation`, `error_handling`, `dead_code_removal`)
- `question`: a clear review question that captures what the team checks for, written so a reviewer could answer it against a new diff
- `description`: one sentence explaining the theme
- Which comments belong to this cluster (by index)

**Also create patterns from the non-comment signals:**

- For each reverted PR in `reverts.json`: if the original PR had NO review comments (review missed it entirely), create or strengthen a pattern for the file categories involved. These are the team's blind spots — the most valuable patterns.
- For each safe change type in `safe_changes.json` with 100% safe rate and 3+ occurrences: note it as a "skip" pattern — these don't need human review.
- For CI checks in `summary.json`: note which checks CI already runs so patterns don't duplicate them. If reviewers consistently flag something that CI also catches (e.g. lint, type errors), mark that pattern as `"covered_by_ci": true`.

Calculate action rates for comment-based clusters from the `acted_on` fields (acted / (acted + dismissed)).

Build file triggers from the paths of comments in each cluster.

Write the semantic patterns to `.prism/patterns.json`, replacing the structural patterns. Each pattern should have: id, name, question, description, trigger, severity, stats, examples, source ("learned" | "revert" | "manual"), and optionally `covered_by_ci`, `safe_to_skip`.

## Step 4: Synthesize Pattern Relationships

Look at the patterns you just created. Identify meaningful relationships:

- **depends_on**: if pattern A finds an issue, pattern B should also be checked (e.g. input_validation → test_coverage)
- **co_occurs**: patterns that increase risk when both fire together
- **contradicts**: if pattern A fires, pattern B is less relevant

Write 3-10 edges to `.prism/edges.json`:

```json
[
  {
    "source": "PAT-001",
    "target": "PAT-003",
    "type": "depends_on",
    "reason": "If inputs aren't validated, verify tests cover the edge cases"
  }
]
```

## Step 5: Update PATTERNS.md

Regenerate `.prism/PATTERNS.md` with the semantic patterns, including questions, descriptions, and relationships.

## Step 6: Create Supporting Files

Create `.prism/feedback.jsonl` as an empty file (append-only feedback log):
```
(empty file — one JSON object per line will be appended here during reviews)
```

Run the calibration engine to generate `calibration.json` from the learned patterns:
```bash
python3 .prism/scripts/scrape.py calibrate --prism-dir .prism --risk-level MEDIUM
```

This creates `.prism/calibration.json` with precomputed gate decisions. With no feedback yet, all patterns start with weak uniform priors — calibration will sharpen as reviews happen.

Create `.prism/rules.json`:
```json
{
  "rules": []
}
```

## Step 7: Report

Tell the user:

1. How many PRs were analyzed
2. How many human review comments were found
3. How many patterns were extracted
4. Which patterns have the highest confidence (team acts on them most)
5. Which patterns are noise (team rarely acts on them)
6. That they should commit `.prism/patterns.json`, `.prism/summary.json`, `.prism/calibration.json`, `.prism/feedback.jsonl`, and `.prism/PATTERNS.md`
7. That `.prism/history/` is gitignored (raw scraped data stays local)

Suggest running `/prism-review <PR_NUMBER>` to try it on a recent PR.
