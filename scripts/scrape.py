#!/usr/bin/env python3
"""Scrape merged PR review history from GitHub and extract raw review data.

Pure data pipeline — no LLM calls, no API keys. Semantic clustering
and edge synthesis are done by the agent running /prism-init, not here.

Usage:
    python3 scrape.py --repo your-org/your-repo --months 6
    python3 scrape.py --repo your-org/your-repo --months 3 --output .prism
"""

import argparse
import json
import math
import random
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path


def gh(endpoint: str, paginate: bool = False) -> list | dict:
    cmd = ["gh", "api", endpoint, "--cache", "1h"]
    if paginate:
        cmd.append("--paginate")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  gh api error: {result.stderr.strip()}", file=sys.stderr)
        return [] if paginate else {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        chunks = result.stdout.strip().split("\n")
        data = []
        for chunk in chunks:
            try:
                parsed = json.loads(chunk)
                if isinstance(parsed, list):
                    data.extend(parsed)
                else:
                    data.append(parsed)
            except json.JSONDecodeError:
                continue
    return data


def fetch_merged_prs(repo: str, since: datetime, max_prs: int = 200) -> list[dict]:
    merged = []
    page = 1
    while len(merged) < max_prs:
        batch = gh(
            f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100&page={page}",
        )
        if not isinstance(batch, list) or not batch:
            break

        oldest_in_batch = None
        for pr in batch:
            if not pr.get("merged_at"):
                continue
            try:
                merged_at = datetime.fromisoformat(
                    pr["merged_at"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                continue
            if merged_at < since:
                oldest_in_batch = merged_at
                continue
            merged.append(pr)

        if oldest_in_batch and oldest_in_batch < since:
            break
        page += 1
        if page > 20:
            break

    print(f"  Found {len(merged)} merged PRs since {since.date()}")
    return merged[:max_prs]


def fetch_pr_details(repo: str, pr_number: int) -> dict:
    return gh(f"repos/{repo}/pulls/{pr_number}")


def fetch_pr_files(repo: str, pr_number: int) -> list[dict]:
    files = gh(f"repos/{repo}/pulls/{pr_number}/files?per_page=100", paginate=True)
    return files if isinstance(files, list) else []


def fetch_review_comments(repo: str, pr_number: int) -> list[dict]:
    comments = gh(
        f"repos/{repo}/pulls/{pr_number}/comments?per_page=100", paginate=True
    )
    return comments if isinstance(comments, list) else []


def fetch_reviews(repo: str, pr_number: int) -> list[dict]:
    reviews = gh(f"repos/{repo}/pulls/{pr_number}/reviews?per_page=100", paginate=True)
    return reviews if isinstance(reviews, list) else []



def is_bot(user: dict | None) -> bool:
    if not user:
        return True
    login = user.get("login", "")
    user_type = user.get("type", "")
    return user_type == "Bot" or login.endswith("[bot]") or login.endswith("-bot")


BOT_KEYWORDS = {"dependabot", "renovate", "snyk", "codecov", "sonar", "unblocked"}


def is_bot_by_name(login: str) -> bool:
    login_lower = login.lower()
    return any(kw in login_lower for kw in BOT_KEYWORDS)


def _fetch_ci_status(repo: str, sha: str) -> list[dict]:
    """Fetch CI check results for a commit."""
    if not sha:
        return []
    data = gh(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    if not isinstance(data, dict):
        return []
    runs = data.get("check_runs", [])
    return [
        {
            "name": r.get("name", ""),
            "conclusion": r.get("conclusion", ""),
        }
        for r in runs
        if r.get("name")
    ]


def find_reverted_prs(repo: str, reviews: list[dict]) -> list[dict]:
    """Scan merged PRs for revert PRs and link them to the original."""
    reverts = []
    merged_by_number = {r["number"]: r for r in reviews}

    for pr in reviews:
        title = pr.get("title", "")
        if not title.lower().startswith("revert"):
            continue

        original_number = None
        import re
        match = re.search(r"#(\d+)", title)
        if match:
            original_number = int(match.group(1))

        original = merged_by_number.get(original_number)

        reverts.append(
            {
                "revert_pr": pr["number"],
                "revert_title": title,
                "original_pr": original_number,
                "original_title": original.get("title", "") if original else None,
                "original_categories": original.get("file_categories", []) if original else [],
                "original_files": original.get("files", []) if original else [],
                "original_had_comments": (original.get("comment_count", 0) > 0) if original else None,
            }
        )

    return reverts


def categorize_file(filepath: str) -> list[str]:
    """Derive categories from a file path. No hardcoded patterns -- uses
    directory structure and extension to produce natural groupings."""
    categories = []
    parts = Path(filepath).parts
    ext = Path(filepath).suffix.lstrip(".")

    if ext:
        categories.append(f"ext:{ext}")

    for part in parts[:-1]:
        if part in (".", ".."):
            continue
        categories.append(f"dir:{part}")

    if len(parts) >= 2:
        categories.append(f"path:{'/'.join(parts[:-1])}")

    return categories


def categorize_files(files: list[dict]) -> set[str]:
    categories = set()
    for f in files:
        path = f.get("filename", "")
        categories.update(categorize_file(path))
    return categories


def _build_commit_file_index(
    repo: str, commits: list[dict], comment_dates: list[str]
) -> dict[str, set[str]]:
    """Build an index of which files changed in which commits.
    Only fetches commit details for commits that happened after at least
    one comment, reducing API calls significantly."""
    if not comment_dates:
        return {}

    earliest_comment = min(d for d in comment_dates if d)
    relevant_commits = [
        c for c in commits
        if c.get("commit", {}).get("committer", {}).get("date", "") > earliest_comment
    ]

    index: dict[str, set[str]] = {}
    for commit in relevant_commits:
        sha = commit.get("sha", "")
        if not sha:
            continue
        detail = gh(f"repos/{repo}/commits/{sha}")
        changed = set()
        for f in detail.get("files", []):
            filename = f.get("filename", "")
            if filename:
                changed.add(filename)
                patch = f.get("patch", "")
                if patch:
                    for line in patch.split("\n"):
                        if line.startswith("@@"):
                            changed.add(f"{filename}:{line}")
        commit_date = commit.get("commit", {}).get("committer", {}).get("date", "")
        index[commit_date] = changed
    return index


def was_comment_acted_on(
    comment: dict, commit_index: dict[str, set[str]]
) -> bool | None:
    comment_date = comment.get("created_at", "")
    comment_path = comment.get("path", "")
    comment_line = comment.get("line") or comment.get("original_line")
    if not comment_date or not comment_path:
        return None

    later_dates = [d for d in commit_index if d > comment_date]
    if not later_dates:
        return None

    for date in later_dates:
        changed = commit_index[date]
        if comment_path in changed:
            return True

    return False


def process_pr(repo: str, pr_summary: dict) -> dict | None:
    pr_number = pr_summary.get("number")
    if not pr_number:
        return None

    print(f"  Processing PR #{pr_number}: {pr_summary.get('title', '')[:60]}")

    details = fetch_pr_details(repo, pr_number)
    if not details or not details.get("merged_at"):
        return None

    files = fetch_pr_files(repo, pr_number)
    review_comments = fetch_review_comments(repo, pr_number)
    reviews = fetch_reviews(repo, pr_number)
    commits = gh(
        f"repos/{repo}/pulls/{pr_number}/commits?per_page=100", paginate=True
    )
    if not isinstance(commits, list):
        commits = []

    human_review_comments = []
    for comment in review_comments:
        user = comment.get("user", {})
        if is_bot(user) or is_bot_by_name(user.get("login", "")):
            continue
        human_review_comments.append(comment)

    comment_dates = [c.get("created_at", "") for c in human_review_comments]
    commit_index = _build_commit_file_index(repo, commits, comment_dates) if comment_dates else {}

    human_comments = []
    for comment in human_review_comments:
        acted_on = was_comment_acted_on(comment, commit_index)

        human_comments.append(
            {
                "body": comment.get("body", ""),
                "path": comment.get("path", ""),
                "line": comment.get("line") or comment.get("original_line"),
                "acted_on": acted_on,
                "created_at": comment.get("created_at", ""),
            }
        )

    for review in reviews:
        user = review.get("user", {})
        if is_bot(user) or is_bot_by_name(user.get("login", "")):
            continue
        body = (review.get("body") or "").strip()
        if not body or body.lower() in ("", "lgtm", "👍", ":+1:", "approved"):
            continue
        if len(body) > 20:
            human_comments.append(
                {
                    "body": body,
                    "path": "",
                    "line": None,
                    "acted_on": None,
                    "created_at": review.get("submitted_at", ""),
                }
            )

    categories = categorize_files(files)

    was_reverted = False
    pr_title = (pr_summary.get("title") or "").lower()
    if "revert" in pr_title:
        was_reverted = True
    for review in reviews:
        body = (review.get("body") or "").lower()
        if "revert" in body:
            was_reverted = True

    human_review_states = [
        r.get("state") for r in reviews
        if not is_bot(r.get("user")) and not is_bot_by_name(r.get("user", {}).get("login", ""))
    ]
    approved_without_comments = (
        "APPROVED" in human_review_states and len(human_comments) == 0
    )

    ci_checks = _fetch_ci_status(repo, details.get("merge_commit_sha", ""))

    return {
        "number": pr_number,
        "title": pr_summary.get("title", ""),
        "merged_at": details.get("merged_at", ""),
        "additions": details.get("additions", 0),
        "deletions": details.get("deletions", 0),
        "changed_files": details.get("changed_files", 0),
        "file_categories": sorted(categories),
        "files": [f.get("filename", "") for f in files],
        "human_comments": human_comments,
        "comment_count": len(human_comments),
        "was_reverted": was_reverted,
        "approved_without_comments": approved_without_comments,
        "ci_checks": ci_checks,
        "time_to_merge_hours": _time_to_merge(pr_summary, details),
    }


def _time_to_merge(summary: dict, details: dict) -> float | None:
    created = summary.get("created_at")
    merged = details.get("merged_at")
    if not created or not merged:
        return None
    try:
        c = datetime.fromisoformat(created.replace("Z", "+00:00"))
        m = datetime.fromisoformat(merged.replace("Z", "+00:00"))
        return round((m - c).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        return None


def _build_trigger_from_paths(file_paths: list[str]) -> dict:
    """Generate glob triggers from actual file paths seen in reviews."""
    extensions: dict[str, int] = defaultdict(int)
    directories: dict[str, int] = defaultdict(int)

    for fp in file_paths:
        ext = Path(fp).suffix
        if ext:
            extensions[ext] += 1
        parent = str(Path(fp).parent)
        if parent and parent != ".":
            directories[parent] += 1

    globs = []

    for ext, count in sorted(extensions.items(), key=lambda x: -x[1]):
        if count >= 2:
            globs.append(f"**/*{ext}")

    for directory, count in sorted(directories.items(), key=lambda x: -x[1]):
        if count >= 2:
            globs.append(f"**/{directory}/**")

    if not globs:
        globs = [f"**/*{ext}" for ext in extensions] or ["**/*"]

    seen = set()
    deduped = []
    for g in globs:
        if g not in seen:
            seen.add(g)
            deduped.append(g)

    return {"files": deduped[:5]}


def build_patterns(reviews: list[dict]) -> list[dict]:
    """Cluster review comments by file location and compute action rates.
    Categories emerge from the data — no hardcoded file types."""

    comment_by_category: dict[str, list[dict]] = defaultdict(list)
    paths_by_category: dict[str, list[str]] = defaultdict(list)

    for pr in reviews:
        for comment in pr.get("human_comments", []):
            comment_path = comment.get("path", "")
            if not comment_path:
                continue

            file_cats = categorize_file(comment_path)

            dir_cats = [c for c in file_cats if c.startswith("dir:")]
            if dir_cats:
                for cat in dir_cats:
                    comment_by_category[cat].append(comment)
                    paths_by_category[cat].append(comment_path)
            else:
                ext_cats = [c for c in file_cats if c.startswith("ext:")]
                for cat in ext_cats:
                    comment_by_category[cat].append(comment)
                    paths_by_category[cat].append(comment_path)

    patterns = []
    pat_id = 1

    scored = []
    for category, comments in comment_by_category.items():
        if len(comments) < 3:
            continue

        acted = [c for c in comments if c.get("acted_on") is True]
        dismissed = [c for c in comments if c.get("acted_on") is False]
        unknown = [c for c in comments if c.get("acted_on") is None]

        known_count = len(acted) + len(dismissed)
        if known_count == 0:
            action_rate = 0.5
        else:
            action_rate = round(len(acted) / known_count, 2)

        scored.append((category, comments, acted, dismissed, unknown, action_rate))

    scored.sort(key=lambda x: (-x[5], -len(x[1])))

    for category, comments, acted, dismissed, unknown, action_rate in scored:
        if action_rate >= 0.8:
            severity = "must_fix"
        elif action_rate >= 0.5:
            severity = "concern"
        elif action_rate >= 0.2:
            severity = "nit"
        else:
            severity = "suppressed"

        trigger = _build_trigger_from_paths(paths_by_category[category])

        example_bodies = []
        for c in acted[:3] + dismissed[:2]:
            body = c.get("body", "").strip()
            if body and len(body) < 2000:
                example_bodies.append(body[:500])

        label = category.split(":", 1)[1] if ":" in category else category

        patterns.append(
            {
                "id": f"PAT-{pat_id:03d}",
                "name": f"{label}_review",
                "category": category,
                "trigger": trigger,
                "severity": severity,
                "stats": {
                    "total_comments": len(comments),
                    "acted_on": len(acted),
                    "dismissed": len(dismissed),
                    "unknown": len(unknown),
                    "action_rate": action_rate,
                },
                "examples": example_bodies[:5],
                "source": "learned",
            }
        )
        pat_id += 1

    return patterns


def build_summary(reviews: list[dict], patterns: list[dict]) -> dict:
    total_prs = len(reviews)
    total_comments = sum(pr.get("comment_count", 0) for pr in reviews)

    category_counts: dict[str, int] = defaultdict(int)
    for pr in reviews:
        for cat in pr.get("file_categories", []):
            category_counts[cat] += 1

    reverted = sum(1 for pr in reviews if pr.get("was_reverted"))
    zero_comment_approvals = sum(1 for pr in reviews if pr.get("approved_without_comments"))

    ci_check_names: dict[str, int] = defaultdict(int)
    for pr in reviews:
        for check in pr.get("ci_checks", []):
            if check.get("conclusion") == "success":
                ci_check_names[check["name"]] += 1

    merge_times = [
        pr["time_to_merge_hours"]
        for pr in reviews
        if pr.get("time_to_merge_hours") is not None
    ]
    avg_merge_time = round(sum(merge_times) / len(merge_times), 1) if merge_times else None

    return {
        "total_prs_analyzed": total_prs,
        "total_human_comments": total_comments,
        "total_reverts": reverted,
        "zero_comment_approvals": zero_comment_approvals,
        "zero_comment_approval_rate": round(zero_comment_approvals / total_prs, 2) if total_prs else 0,
        "avg_merge_time_hours": avg_merge_time,
        "ci_checks_available": dict(sorted(ci_check_names.items(), key=lambda x: -x[1])[:15]),
        "prs_by_category": dict(sorted(category_counts.items(), key=lambda x: -x[1])),
        "pattern_count": len(patterns),
    }


def write_patterns_md(patterns: list[dict], summary: dict, edges: list[dict], output_dir: Path):
    lines = [
        "# Review Patterns",
        "",
        f"Auto-generated by Prism on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}.",
        f"Analyzed {summary['total_prs_analyzed']} merged PRs with {summary['total_human_comments']} human review comments.",
        "",
        "## Summary",
        "",
        f"- **PRs analyzed**: {summary['total_prs_analyzed']}",
        f"- **Human review comments**: {summary['total_human_comments']}",
        f"- **Reverted PRs**: {summary['total_reverts']}",
        f"- **Avg time to merge**: {summary['avg_merge_time_hours']}h" if summary['avg_merge_time_hours'] else "",
        f"- **Patterns extracted**: {summary['pattern_count']}",
        "",
    ]

    high = [p for p in patterns if p["stats"]["action_rate"] >= 0.8]
    medium = [p for p in patterns if 0.4 <= p["stats"]["action_rate"] < 0.8]
    low = [p for p in patterns if p["stats"]["action_rate"] < 0.4]

    def _write_pattern(p: dict, lines: list[str]):
        s = p["stats"]
        lines.append(f"### {p['name']} ({s['action_rate']*100:.0f}% action rate)")
        if p.get("description"):
            lines.append(f"*{p['description']}*")
        lines.append(f"Surfaced {s['total_comments']} times. Acted on {s['acted_on']}, dismissed {s['dismissed']}.")
        lines.append(f"Severity: **{p['severity']}**")
        if p.get("question"):
            lines.append(f"\n**Review question:** {p['question']}")
        if p.get("examples"):
            lines.append("\nExample comments from your team:")
            for ex in p["examples"][:3]:
                lines.append(f"> {ex}")
        pat_edges = [e for e in edges if e.get("source") == p["id"] or e.get("target") == p["id"]]
        if pat_edges:
            lines.append("\n**Connected to:**")
            for e in pat_edges:
                other = e["target"] if e["source"] == p["id"] else e["source"]
                lines.append(f"- {e['type']} → {other}: {e.get('reason', '')}")
        lines.append("")

    if high:
        lines.extend(["## High Confidence (team acts on these >80% of the time)", ""])
        for p in high:
            _write_pattern(p, lines)

    if medium:
        lines.extend(["## Medium Confidence (40-80% action rate)", ""])
        for p in medium:
            _write_pattern(p, lines)

    if low:
        lines.extend(["## Low Confidence / Noise (<40% action rate)", ""])
        for p in low:
            s = p["stats"]
            lines.append(f"- **{p['name']}**: {s['action_rate']*100:.0f}% action rate ({s['total_comments']} comments)")
        lines.append("")

    if edges:
        lines.extend(["## Pattern Relationships", ""])
        for e in edges:
            lines.append(f"- **{e['source']}** —{e['type']}→ **{e['target']}**: {e.get('reason', '')}")
        lines.append("")

    (output_dir / "PATTERNS.md").write_text("\n".join(lines))


# ─── CALIBRATION ENGINE ──────────────────────────────────────────────────────


def _incomplete_beta(a: float, b: float, x: float, iterations: int = 200) -> float:
    """Regularized incomplete beta function I_x(a, b) via continued fraction."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    # Use symmetry relation for better convergence when x > (a+1)/(a+b+2)
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _incomplete_beta(b, a, 1.0 - x, iterations)
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1.0 - x) * b - lbeta) / a
    # Lentz's continued fraction
    cf = 1.0
    c = 1.0
    d = 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / d if abs(d) > 1e-30 else 1e30
    cf = d
    for m in range(1, iterations + 1):
        # Even step
        numerator = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        d = 1.0 + numerator * d
        c = 1.0 + numerator / c
        d = 1.0 / d if abs(d) > 1e-30 else 1e30
        c = c if abs(c) > 1e-30 else 1e-30
        cf *= d * c
        # Odd step
        numerator = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + numerator * d
        c = 1.0 + numerator / c
        d = 1.0 / d if abs(d) > 1e-30 else 1e30
        c = c if abs(c) > 1e-30 else 1e-30
        delta = d * c
        cf *= delta
        if abs(delta - 1.0) < 1e-10:
            break
    return front * cf


def _p_below_threshold(alpha: float, beta: float, threshold: float = 0.15) -> float:
    """P(true action rate < threshold) given Beta(alpha, beta) posterior."""
    return _incomplete_beta(alpha, beta, threshold)


def calibrate_pattern(
    pattern: dict,
    feedback_entries: list[dict],
    now: datetime,
    category_base_rate: float | None = None,
) -> dict:
    """Compute Bayesian calibration for a single pattern.

    Returns a calibration dict with alpha, beta, effective_rate, uncertainty,
    suppress_probability, show, thompson_sample, and reason.
    """
    # Prior: use category base rate if available and pattern has few observations,
    # else use weak uniform Beta(1, 1)
    if category_base_rate is not None and len(feedback_entries) < 5:
        # Convert base rate to pseudo-count equivalent (strength = 4 observations)
        strength = 4.0
        alpha = 1.0 + category_base_rate * strength
        beta = 1.0 + (1.0 - category_base_rate) * strength
    else:
        alpha = 1.0
        beta = 1.0

    # Update with time-decayed feedback entries
    for entry in feedback_entries:
        ts_str = entry.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = now
        weeks_ago = max(0.0, (now - ts).total_seconds() / (7 * 86400))
        weight = 0.97 ** weeks_ago  # ~0.22x at 6 months, ~0.13x at 9 months
        weight *= entry.get("weight_boost", 1.0)  # post-incident boost for reverts

        action = entry.get("action", "")
        if action == "confirm":
            alpha += weight
        elif action == "dismiss":
            beta += weight

    effective_rate = alpha / (alpha + beta)

    # 80% credible interval width as uncertainty measure
    # Use normal approximation: ±1.28 * sqrt(alpha*beta / ((a+b)^2 * (a+b+1)))
    n = alpha + beta
    variance = (alpha * beta) / (n * n * (n + 1))
    uncertainty = 2 * 1.28 * math.sqrt(variance) if variance > 0 else 0.0

    suppress_probability = _p_below_threshold(alpha, beta, threshold=0.15)
    should_suppress = suppress_probability > 0.90

    # Thompson sampling: sample from the posterior for re-exploration
    thompson_sample = random.betavariate(max(alpha, 0.1), max(beta, 0.1))

    # Determine show: suppress only if statistically confident act rate < 15%,
    # BUT allow re-exploration via Thompson sampling with a 20% threshold
    show = not should_suppress or thompson_sample > 0.20

    if should_suppress and show:
        reason = f"suppressed ({effective_rate*100:.0f}% rate) but re-exploring via Thompson sampling"
    elif should_suppress:
        reason = f"suppressed: 90%+ confident true rate < 15% (effective rate {effective_rate*100:.0f}%)"
    elif effective_rate >= 0.80:
        reason = f"high confidence: {effective_rate*100:.0f}% effective rate"
    elif effective_rate >= 0.50:
        reason = f"medium confidence: {effective_rate*100:.0f}% effective rate"
    else:
        reason = f"low confidence: {effective_rate*100:.0f}% effective rate (show depends on risk level)"

    return {
        "alpha": round(alpha, 3),
        "beta": round(beta, 3),
        "effective_rate": round(effective_rate, 4),
        "uncertainty": round(uncertainty, 4),
        "suppress_probability": round(suppress_probability, 4),
        "should_suppress": should_suppress,
        "show": show,
        "thompson_sample": round(thompson_sample, 4),
        "reason": reason,
    }


def compute_review_gate(
    patterns: list[dict],
    feedback_entries: list[dict],
    risk_level: str = "MEDIUM",
    prism_dir: Path | None = None,
) -> dict:
    """Compute gate decisions for all patterns given current feedback and risk level.

    Returns a dict keyed by pattern_id with gate decisions.
    """
    now = datetime.now(timezone.utc)

    # Group feedback by pattern_id
    feedback_by_pattern: dict[str, list[dict]] = defaultdict(list)
    for entry in feedback_entries:
        pid = entry.get("pattern_id", "")
        if pid:
            feedback_by_pattern[pid].append(entry)

    # Phase 4: compute category-level base rates for hierarchical priors
    category_rates: dict[str, list[float]] = defaultdict(list)
    for p in patterns:
        cat = p.get("category", "")
        pid = p.get("id", "")
        entries = feedback_by_pattern.get(pid, [])
        if len(entries) >= 5:
            # Only use patterns with enough data as anchors
            cal = calibrate_pattern(p, entries, now, category_base_rate=None)
            if cat:
                category_rates[cat].append(cal["effective_rate"])

    category_base_rates: dict[str, float] = {
        cat: sum(rates) / len(rates)
        for cat, rates in category_rates.items()
        if rates
    }

    gate = {}
    for p in patterns:
        pid = p.get("id", "")
        entries = feedback_by_pattern.get(pid, [])
        cat = p.get("category", "")
        base_rate = category_base_rates.get(cat)

        cal = calibrate_pattern(p, entries, now, category_base_rate=base_rate)

        # Risk-level adjustment: at HIGH risk, show medium-confidence patterns
        # that would otherwise be borderline
        show = cal["show"]
        if not cal["should_suppress"]:
            rate = cal["effective_rate"]
            if risk_level == "LOW" and rate < 0.50:
                show = False
            elif risk_level == "MEDIUM" and rate < 0.30:
                show = False
            # HIGH risk: show everything not statistically suppressed

        gate[pid] = {
            **cal,
            "show": show,
            "risk_level": risk_level,
        }

    return gate


def update_calibration_file(prism_dir: Path, new_entry: dict) -> None:
    """Append a feedback entry to feedback.jsonl."""
    feedback_file = prism_dir / "feedback.jsonl"
    if not new_entry.get("timestamp"):
        new_entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with feedback_file.open("a") as f:
        f.write(json.dumps(new_entry) + "\n")


def load_feedback_jsonl(prism_dir: Path) -> list[dict]:
    """Load all entries from feedback.jsonl."""
    feedback_file = prism_dir / "feedback.jsonl"
    if not feedback_file.exists():
        return []
    entries = []
    for line in feedback_file.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def cmd_calibrate(args: argparse.Namespace) -> None:
    """Recompute calibration.json from patterns.json and feedback.jsonl."""
    prism_dir = Path(args.prism_dir)
    patterns_file = prism_dir / "patterns.json"
    if not patterns_file.exists():
        print(f"Error: {patterns_file} not found. Run /prism-init first.", file=sys.stderr)
        sys.exit(1)

    patterns = json.loads(patterns_file.read_text())
    feedback = load_feedback_jsonl(prism_dir)

    risk_level = getattr(args, "risk_level", "MEDIUM").upper()
    if risk_level not in ("LOW", "MEDIUM", "HIGH"):
        risk_level = "MEDIUM"

    gate = compute_review_gate(patterns, feedback, risk_level=risk_level, prism_dir=prism_dir)

    calibration = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "risk_level": risk_level,
        "total_feedback_entries": len(feedback),
        "patterns": gate,
    }

    (prism_dir / "calibration.json").write_text(json.dumps(calibration, indent=2))

    shown = sum(1 for v in gate.values() if v["show"])
    suppressed = len(gate) - shown
    print(f"Calibration updated: {shown} patterns shown, {suppressed} suppressed (risk: {risk_level})")


# ─── IMPLICIT SIGNAL COLLECTION (Phase 3) ────────────────────────────────────


def cmd_track(args: argparse.Namespace) -> None:
    """Check post-review implicit signals: did authors fix flagged files? Any reverts?"""
    prism_dir = Path(args.prism_dir)
    feedback_file = prism_dir / "feedback.jsonl"
    if not feedback_file.exists():
        print("No feedback.jsonl found. Run a review first.", file=sys.stderr)
        return

    feedback = load_feedback_jsonl(prism_dir)
    # Find reviewed PRs with explicit feedback
    reviewed_prs: dict[int, dict] = {}
    for entry in feedback:
        if entry.get("source", "explicit") == "explicit":
            pr_num = entry.get("pr")
            if pr_num:
                if pr_num not in reviewed_prs:
                    reviewed_prs[pr_num] = {"timestamp": entry["timestamp"], "findings": []}
                reviewed_prs[pr_num]["findings"].append(entry)

    if not reviewed_prs:
        print("No reviewed PRs found in feedback.jsonl.")
        return

    repo = args.repo
    new_implicit = 0
    now = datetime.now(timezone.utc)

    for pr_num, review_data in reviewed_prs.items():
        review_time_str = review_data["timestamp"]
        try:
            review_time = datetime.fromisoformat(review_time_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        # Fetch commits on this PR after the review timestamp
        commits_data = gh(f"repos/{repo}/pulls/{pr_num}/commits")
        if not isinstance(commits_data, list):
            continue

        post_review_commits = []
        for commit in commits_data:
            commit_date_str = (commit.get("commit", {}) or {}).get("author", {}).get("date", "")
            try:
                commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if commit_date > review_time:
                post_review_commits.append(commit.get("sha", ""))

        if not post_review_commits:
            continue

        # Get files changed in post-review commits
        changed_after: set[str] = set()
        for sha in post_review_commits:
            commit_detail = gh(f"repos/{repo}/commits/{sha}")
            for f in (commit_detail.get("files") or []):
                changed_after.add(f.get("filename", ""))

        # Check if any finding's file was changed after review
        for finding in review_data["findings"]:
            pattern_id = finding.get("pattern_id", "")
            file_path = finding.get("file", "")
            if not file_path or not pattern_id:
                continue

            # Check if any changed file matches (by prefix for directory patterns)
            was_fixed = any(
                f == file_path or f.startswith(file_path.rstrip("/") + "/")
                for f in changed_after
            )

            if was_fixed:
                implicit_entry = {
                    "pattern_id": pattern_id,
                    "pr": pr_num,
                    "action": "confirm",
                    "reason": None,
                    "timestamp": now.isoformat(),
                    "source": "implicit_commit_after_review",
                }
                update_calibration_file(prism_dir, implicit_entry)
                new_implicit += 1

        # Check if this PR was later reverted
        # Search for a "Revert" PR that references this PR number
        search_results = gh(f"repos/{repo}/pulls?state=closed&per_page=20")
        if isinstance(search_results, list):
            for candidate in search_results:
                title = candidate.get("title", "")
                body = candidate.get("body", "") or ""
                if title.lower().startswith("revert") and (
                    f"#{pr_num}" in title or f"#{pr_num}" in body
                ):
                    # This PR was reverted — boost findings that were dismissed on it
                    for finding in review_data["findings"]:
                        if finding.get("action") == "dismiss":
                            revert_entry = {
                                "pattern_id": finding.get("pattern_id", ""),
                                "pr": pr_num,
                                "action": "confirm",
                                "reason": f"post-incident: PR #{pr_num} was reverted",
                                "timestamp": now.isoformat(),
                                "source": "implicit_revert",
                                "weight_boost": 2.0,
                            }
                            update_calibration_file(prism_dir, revert_entry)
                            new_implicit += 1
                    break

    print(f"Tracking complete: {new_implicit} implicit feedback entries added.")
    if new_implicit > 0:
        print("Run 'python3 scrape.py calibrate' to update calibration.json.")


# ─── MAIN ─────────────────────────────────────────────────────────────────────


def main():
    # Detect subcommand vs legacy flat usage
    subcommands = {"scrape", "calibrate", "track"}
    first_positional = next((a for a in sys.argv[1:] if not a.startswith("-")), None)

    if first_positional in subcommands:
        # Subcommand mode
        parser = argparse.ArgumentParser(description="Prism — PR review history scraper and calibration engine")
        subparsers = parser.add_subparsers(dest="command")

        scrape_parser = subparsers.add_parser("scrape", help="Scrape PR history and build patterns")
        scrape_parser.add_argument("--repo", required=True)
        scrape_parser.add_argument("--months", type=int, default=6)
        scrape_parser.add_argument("--output", default=".prism")
        scrape_parser.add_argument("--max-prs", type=int, default=100)

        cal_parser = subparsers.add_parser("calibrate", help="Recompute calibration.json from feedback")
        cal_parser.add_argument("--prism-dir", default=".prism")
        cal_parser.add_argument("--risk-level", default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"])

        track_parser = subparsers.add_parser("track", help="Collect implicit feedback signals from GitHub")
        track_parser.add_argument("--repo", required=True)
        track_parser.add_argument("--prism-dir", default=".prism")

        args = parser.parse_args()
        if args.command == "scrape":
            _run_scrape(args)
        elif args.command == "calibrate":
            cmd_calibrate(args)
        elif args.command == "track":
            cmd_track(args)
        else:
            parser.print_help()
    else:
        # Legacy flat mode: python3 scrape.py --repo owner/repo [--months N] [--output DIR]
        parser = argparse.ArgumentParser(description="Scrape PR review history and build patterns")
        parser.add_argument("--repo", required=True, help="GitHub repo (e.g. owner/repo)")
        parser.add_argument("--months", type=int, default=6)
        parser.add_argument("--output", default=".prism")
        parser.add_argument("--max-prs", type=int, default=100)
        args = parser.parse_args()
        _run_scrape(args)


def _run_scrape(args: argparse.Namespace) -> None:
    """Original scrape logic, extracted for clean subcommand routing."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "history").mkdir(exist_ok=True)

    (output_dir / ".gitignore").write_text("history/\n")

    since = datetime.now(timezone.utc) - timedelta(days=args.months * 30)

    print(f"\nPrism — Scraping review history for {args.repo}")
    print(f"  Looking back {args.months} months (since {since.date()})")
    print()

    print("Phase 1: Fetching merged PRs...")
    prs = fetch_merged_prs(args.repo, since, max_prs=args.max_prs)

    if not prs:
        print("No merged PRs found. Check repo name and date range.")
        sys.exit(1)

    checkpoint_file = output_dir / "history" / "checkpoint.json"
    reviews = []
    start_index = 0

    if checkpoint_file.exists():
        try:
            checkpoint = json.loads(checkpoint_file.read_text())
            reviews = checkpoint.get("reviews", [])
            processed_prs = {r["number"] for r in reviews}
            prs = [pr for pr in prs if pr.get("number") not in processed_prs]
            start_index = checkpoint.get("processed_count", 0)
            print(f"\n  Resuming from checkpoint: {start_index} PRs already processed, {len(prs)} remaining")
        except (json.JSONDecodeError, KeyError):
            pass

    print(f"\nPhase 2: Processing {len(prs)} PRs (fetching files, comments, commits)...")
    print("  This may take a few minutes for large repos.\n")

    for i, pr in enumerate(prs):
        result = process_pr(args.repo, pr)
        if result:
            reviews.append(result)

        if (i + 1) % 10 == 0:
            print(f"  ... {start_index + i + 1}/{start_index + len(prs)} processed")
            checkpoint_file.write_text(json.dumps(
                {"processed_count": start_index + i + 1, "reviews": reviews},
            ))

    checkpoint_file.write_text(json.dumps(
        {"processed_count": start_index + len(prs), "reviews": reviews},
    ))

    print(f"\n  Processed {len(reviews)} PRs with review data")

    (output_dir / "history" / "reviews.json").write_text(
        json.dumps(reviews, indent=2)
    )

    if checkpoint_file.exists():
        checkpoint_file.unlink()

    print("\nPhase 3: Building structural patterns...")
    patterns = build_patterns(reviews)
    summary = build_summary(reviews, patterns)

    all_comments = []
    for pr in reviews:
        for comment in pr.get("human_comments", []):
            all_comments.append({**comment, "pr_number": pr["number"]})

    reverts = find_reverted_prs(reviews)
    safe_categories = defaultdict(lambda: {"total": 0, "reverted": 0})
    for pr in reviews:
        if pr.get("approved_without_comments"):
            for cat in pr.get("file_categories", []):
                safe_categories[cat]["total"] += 1
                if pr.get("was_reverted"):
                    safe_categories[cat]["reverted"] += 1

    safe_change_types = {
        cat: {
            "total": data["total"],
            "reverted": data["reverted"],
            "safe_rate": round(1 - data["reverted"] / data["total"], 2) if data["total"] > 0 else 1.0,
        }
        for cat, data in safe_categories.items()
        if data["total"] >= 3
    }

    (output_dir / "patterns.json").write_text(json.dumps(patterns, indent=2))
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (output_dir / "history" / "comments.json").write_text(json.dumps(all_comments, indent=2))
    (output_dir / "history" / "reverts.json").write_text(json.dumps(reverts, indent=2))
    (output_dir / "history" / "safe_changes.json").write_text(json.dumps(safe_change_types, indent=2))
    write_patterns_md(patterns, summary, [], output_dir)

    print(f"\n  {len(patterns)} structural patterns from {summary['total_human_comments']} human comments")
    print(f"  {len(all_comments)} raw comments for semantic clustering")
    print(f"  {len(reverts)} reverted PRs detected")
    print(f"  {summary['zero_comment_approvals']}/{summary['total_prs_analyzed']} PRs approved without comments ({summary['zero_comment_approval_rate']*100:.0f}%)")
    if summary.get("ci_checks_available"):
        print(f"  CI checks running: {', '.join(list(summary['ci_checks_available'].keys())[:5])}")
    print()

    high = [p for p in patterns if p["stats"]["action_rate"] >= 0.8]
    low = [p for p in patterns if p["stats"]["action_rate"] < 0.2]

    if high:
        print("  HIGH CONFIDENCE (your team almost always acts on these):")
        for p in high:
            print(f"    {p['name']}: {p['stats']['action_rate']*100:.0f}% action rate ({p['stats']['total_comments']} comments)")

    if low:
        print(f"\n  NOISE ({len(low)} patterns your team mostly ignores)")

    if reverts:
        print(f"\n  REVERTED PRs (review missed something):")
        for r in reverts[:5]:
            orig = f"PR #{r['original_pr']}: {r['original_title']}" if r.get("original_title") else f"PR #{r.get('original_pr', '?')}"
            had_comments = "had review comments" if r.get("original_had_comments") else "NO review comments"
            print(f"    {orig} — {had_comments}")

    if safe_change_types:
        safe_sorted = sorted(safe_change_types.items(), key=lambda x: (-x[1]["safe_rate"], -x[1]["total"]))
        print(f"\n  SAFE CHANGE TYPES (approved without comments, never reverted):")
        for cat, data in safe_sorted[:5]:
            if data["safe_rate"] == 1.0 and data["total"] >= 3:
                print(f"    {cat}: {data['total']} PRs, 0 reverts")

    print(f"\nDone. Output written to {output_dir}/")
    print(f"  patterns.json     — structural patterns (commit this)")
    print(f"  summary.json      — aggregate stats (commit this)")
    print(f"  PATTERNS.md       — human-readable report (commit this)")
    print(f"  history/          — raw data (gitignored)")
    print(f"    comments.json   — all human comments for semantic clustering")
    print(f"    reverts.json    — reverted PRs and what was missed")
    print(f"    safe_changes.json — change types safe to skip review")
    print()
    print("Next: run /prism-init to semantically cluster and generate review questions.")


if __name__ == "__main__":
    main()
