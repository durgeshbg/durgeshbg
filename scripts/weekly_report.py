#!/usr/bin/env python3
"""Update the README's weekly report, commit it, and push to origin/main."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START_MARKER = "<!-- weekly-report:start -->"
END_MARKER = "<!-- weekly-report:end -->"
USERNAME = "durgeshbg"
METRICS = (
    ("Commits", "totalCommitContributions"),
    ("Pull requests opened", "totalPullRequestContributions"),
    ("Issues opened", "totalIssueContributions"),
    ("Pull request reviews", "totalPullRequestReviewContributions"),
    ("Repositories committed to", "totalRepositoriesWithContributedCommits"),
)
QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      totalRepositoriesWithContributedCommits
    }
  }
}
"""


def report_period(today):
    return today - timedelta(days=7), today - timedelta(days=1)


def fetch_contributions(start, end):
    result = subprocess.run(
        [
            "gh", "api", "--hostname", "github.com", "graphql",
            "-f", f"query={QUERY}",
            "-f", f"login={USERNAME}",
            "-f", f"from={start.isoformat()}T00:00:00Z",
            "-f", f"to={end.isoformat()}T23:59:59Z",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    if payload.get("errors"):
        raise ValueError("GitHub returned GraphQL errors; no report generated.")
    return payload["data"]["user"]["contributionsCollection"]


def render_report(contributions, start, end):
    rows = []
    for label, field in METRICS:
        count = contributions[field]
        if type(count) is not int or count < 0:
            raise ValueError(f"Invalid contribution count: {field}")
        rows.append(f"| {label} | {count:,} |")
    return "\n".join([
        "### weekly activity",
        "",
        f"**{start.isoformat()} – {end.isoformat()} (UTC)** · "
        f"[@{USERNAME}](https://github.com/{USERNAME})",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
        *rows,
        "",
        "_Counts follow GitHub contribution rules and the authenticated account's "
        "visibility; they are not a count of every commit made._",
        "",
    ])


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def replace_report(readme, report):
    if readme.count(START_MARKER) != 1 or readme.count(END_MARKER) != 1:
        raise ValueError("README must contain exactly one pair of weekly-report markers.")
    start = readme.index(START_MARKER) + len(START_MARKER)
    end = readme.index(END_MARKER)
    if start > end:
        raise ValueError("README weekly-report markers are out of order.")
    return readme[:start] + "\n\n" + report.rstrip() + "\n\n" + readme[end:]


def publish_report():
    if git("branch", "--show-current") != "main":
        raise ValueError("Switch to main before publishing the weekly report.")
    if git("status", "--porcelain"):
        raise ValueError("Working tree must be clean. Commit or stash your changes first.")
    git("remote", "get-url", "--push", "origin")
    path = ROOT / "README.md"
    original = path.read_text(encoding="utf-8")
    replace_report(original, "")  # Validate markers before requesting GitHub data.
    start, end = report_period(datetime.now(timezone.utc).date())
    report = render_report(fetch_contributions(start, end), start, end)
    updated = replace_report(original, report)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        git("add", "--", "README.md")
        git("commit", "-m", "chore: update weekly activity report", "--", "README.md")
        print("Updated and committed README.md.")
    else:
        print("README.md is already up to date; no new commit.")
    # Retry an earlier failed push even when the report hasn't changed.
    git("push", "origin", "HEAD:refs/heads/main")
    print("Pushed to origin/main.")


def main():
    try:
        publish_report()
    except subprocess.CalledProcessError as error:
        print(f"Command failed: {error.stderr.strip()}", file=sys.stderr)
        print("Local changes/commits were kept. Fix the error and rerun; no force push is used.",
              file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"Could not publish report: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
