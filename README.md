# Prism

**Code review intelligence learned from your team's PR history.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Plugin-blueviolet.svg)](https://claude.ai/code)

Generic AI review tools catch generic bugs. **Prism catches what _your team_ actually cares about** — learned from how your team has reviewed code over the past 6 months.

---

## Table of Contents

- [Why Prism](#why-prism)
- [How It Works](#how-it-works)
- [Quick Start](#quick-start)
- [Commands](#commands)
- [What Gets Committed](#what-gets-committed)
- [Calibration](#calibration)
- [Requirements](#requirements)
- [License](#license)

---

## Why Prism

AI-assisted coding has created a quiet bottleneck: more code, same number of reviewers.

- PR volume is up 20–40% since AI coding tools became mainstream
- Reviewer capacity hasn't changed
- Generic tools (BugBot, CodeRabbit) find generic issues — they don't know that your team always requires `permission_classes`, or that caching suggestions get ignored 90% of the time

Your team's PR history already contains that signal. Prism reads it.

When a reviewer flags an issue and the author fixes it — that's a pattern worth encoding.
When a reviewer suggests something and the author ignores it consistently — that's noise worth suppressing.

---

## How It Works

### 1. Init: learn from history

`/prism-init` scrapes your repo's merged PRs via the GitHub API. For each PR it:

1. Gets the changed files and categorizes them (migration, API, frontend, etc.)
2. Gets human review comments (filters out bots)
3. Checks whether each comment was **acted on** (the code at that line changed in a later commit) or **dismissed** (ignored)
4. Clusters comments semantically by category
5. Computes **action rates** — how often the team actually applied each pattern

The result is `.prism/patterns.json`: review patterns with team-calibrated confidence scores.

### 2. Review: risk-scored, confidence-gated

`/prism-review <PR number>` runs a structured 4-pass pipeline:

1. **Risk scoring** — lines changed, patterns triggered, incident history
2. **Specialized passes** — security, correctness, conventions, test coverage
3. **Cross-file context** — findings from file A inform review of file B
4. **Graph traversal** — connected patterns fire even if direct triggers didn't match
5. **Confidence gate** — suppresses low-confidence findings (target: &lt;2 dismissals per review)
6. **Rule exclusions** — checks learned rules before surfacing anything

### 3. Feedback: calibrate with reasons

When you confirm or dismiss a finding, Prism updates the pattern's action rate. For dismissals, it asks *why* and extracts a reusable exclusion rule ("we allow X in test files") so it doesn't repeat the mistake.

Recent feedback is weighted higher (exponential decay, 3-month half-life). Old signal fades. The model stays current.

### 4. Learn: stay fresh

`/prism-learn` re-scrapes recent PRs, merges with existing data, and re-clusters semantically. Run it periodically to keep patterns current as your team's standards evolve.

---

## Quick Start

### Install into your project

```bash
cd your-project
curl -fsSL https://raw.githubusercontent.com/Messier81/prism/main/install.sh | bash
```

Or clone and install manually:

```bash
git clone https://github.com/Messier81/prism
cd prism
./install.sh /path/to/your-project
```

### Initialize

Open Claude Code in your project and run:

```
/prism-init
```

This scrapes 6 months of PR history and builds your team's pattern library. Takes a few minutes depending on repo size.

### Review a PR

```
/prism-review 349
```

---

## Commands

| Command | Description |
|---|---|
| `/prism-init` | Scrape PR history, cluster patterns, initialize feedback log |
| `/prism-review <PR>` | Review a PR using learned team patterns |
| `/prism-patterns` | View and edit your team's current patterns |
| `/prism-learn` | Incrementally update patterns from recent PRs |

---

## What Gets Committed

Commit the `.prism/` pattern files so your whole team benefits. When patterns change, that's a PR the team can review — your review knowledge is itself reviewed.

```
.prism/
├── patterns.json        # semantic patterns with questions + action rates
├── edges.json           # pattern relationship graph
├── rules.json           # learned exclusion rules from dismissals
├── calibration.json     # feedback history with reasons
├── summary.json         # aggregate stats
├── PATTERNS.md          # human-readable report
└── history/             # gitignored (raw scraped data)
```

---

## Calibration

Prism uses **Beta-Binomial calibration** to decide when a pattern is reliable enough to surface:

- A pattern is suppressed when P(action_rate < 15%) > 90%
- New patterns inherit their category's base rate as a prior (hierarchical priors)
- Suppressed patterns are occasionally re-explored via Thompson sampling
- Feedback entries support a `weight_boost` field for post-incident escalation (default: 1.0)

The goal is fewer false positives, not fewer findings. Prism should surface less than 2 dismissals per review on average.

---

## No Names

Prism stores **team-level aggregates only**. No individual reviewer names, no "who caught what." Patterns belong to the team.

---

## Requirements

- [`gh` CLI](https://cli.github.com/) installed and authenticated
- Python 3.8+
- [Claude Code](https://claude.ai/code) (for slash commands), or any agent that can read files and run shell commands

---

## License

MIT — see [LICENSE](LICENSE).
