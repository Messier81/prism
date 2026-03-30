---
description: Review a PR using patterns learned from your team's review history. Calibration-gated, self-verifying, with graph traversal. Use /prism-init first.
argument-hint: "<PR number> [--repo owner/repo] [--verbose] [--post]"
---

# Prism Review

You are reviewing a pull request using this team's learned review patterns.

**Arguments:** $ARGUMENTS

Parse the PR number from arguments. If `--repo` is provided, use that. Otherwise detect from `git remote get-url origin`. If `--verbose` is present, also show suppressed patterns. If `--post` is present, post the review to GitHub after confirmation.

---

## Pre-check

Verify `.prism/patterns.json` exists. If not, tell the user to run `/prism-init` first.

Also verify `.prism/scripts/scrape.py` exists (required for calibration). If missing, the tool was not installed correctly.

## Step 1: Initial Calibration

Before loading patterns, run calibration at MEDIUM risk (will be updated after Step 3):

```bash
python3 .prism/scripts/scrape.py calibrate --prism-dir .prism --risk-level MEDIUM
```

Then read all available Prism files:
- `.prism/calibration.json` — gate decisions (read `patterns.<id>.show`)
- `.prism/patterns.json` — pattern questions, severity, examples
- `.prism/edges.json` — pattern relationships
- `.prism/rules.json` — learned exclusion rules from past dismissals

Skip any that don't exist.

## Step 2: Fetch the PR

```bash
gh pr view <PR_NUMBER> --repo <REPO> --json number,title,body,additions,deletions,changedFiles,files,labels
gh pr diff <PR_NUMBER> --repo <REPO>
```

## Step 3: Risk Score + Recalibrate

Score the PR:
- Lines changed: <100 (1), 100-500 (2), >500 (3)
- Files changed: <5 (1), 5-15 (2), >15 (3)
- High-confidence patterns triggered (action_rate > 0.8): 0 (1), 1-2 (2), 3+ (3)
- Categories with high revert rates (from summary.json): 0 (1), 1 (2), 2+ (3)

Risk: LOW (4-6), MEDIUM (7-9), HIGH (10+)

Now recalibrate with the correct risk level:

```bash
python3 .prism/scripts/scrape.py calibrate --prism-dir .prism --risk-level <LOW|MEDIUM|HIGH>
```

Re-read `.prism/calibration.json`. These are the final gate decisions for this review.

## Step 4: Run the Reviewer Agent

Follow the full process in `.claude/agents/reviewer.md`:

1. Blast radius check (Step 3.5) — detect public API changes with callers outside the PR
2. Run 4 structured passes (security, correctness, conventions, tests) — each writes to `.prism/tmp/pass_N.json`
3. Self-verification pass — verify must_fix diff quotes exist; deduplicate across passes
4. Graph traversal — apply co-occurrence escalation and contradicts suppression
5. Present findings per the format in Step 7

The calibration gate from `calibration.json` is authoritative. The agent must not override `show: false` decisions.

## Step 5: Present Findings

Format per reviewer agent Step 7. Include:
- Risk level and calibration summary at top
- Blast radius warnings if any
- Verified findings grouped by severity
- Multi-pass agreement indicators
- Gated count with `--verbose` hint

If `--verbose` was passed, also show suppressed patterns with their suppression reason from `calibration.json`.

## Step 6: Collect Feedback

After presenting, ask: "Confirm or dismiss each finding? For dismissals, briefly say WHY (one sentence)."

For each response, append to `.prism/feedback.jsonl`:

```json
{"pattern_id": "PAT-XXX", "pr": <number>, "action": "confirm|dismiss", "reason": "reason or null", "timestamp": "<ISO 8601>", "source": "explicit"}
```

Then recompute calibration with current risk level.

## Step 7: Post (if requested)

If `--post` was passed or the user says "post":

```bash
gh pr review <PR_NUMBER> --repo <REPO> --comment --body "<review body>"
```

The review body should start with "**Prism Review** (risk: LOW|MEDIUM|HIGH)" and list confirmed findings with severity and evidence.

## Step 8: Update Patterns and Extract Rules

Per reviewer agent Steps 9 and 10: update `patterns.json` action rates, extract exclusion rules from dismissals, regenerate `PATTERNS.md` if severity changed, clean up `.prism/tmp/`.
