#!/usr/bin/env python3
"""Refresh lifetime GitHub stats in the README, commit, and push to origin/main."""

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
START_MARKER = "<!-- weekly-report:start -->"
END_MARKER = "<!-- weekly-report:end -->"
USERNAME = "durgeshbg"
LANGUAGE_LOGOS = {
    "TypeScript": ("typescript", "3178C6"),
    "JavaScript": ("javascript", "F7DF1E"),
    "Python": ("python", "3776AB"),
    "HTML": ("html5", "E34F26"),
    "CSS": ("css", "663399"),
    "Solidity": ("solidity", "363636"),
    "PHP": ("php", "777BB4"),
    "C": ("c", "A8B9CC"),
    "Rust": ("rust", "A65D38"),
    "Pug": ("pug", "A86454"),
    "Lua": ("lua", "2C2D72"),
    "C++": ("cplusplus", "00599C"),
    "SCSS": ("sass", "CC6699"),
    "Ruby": ("ruby", "CC342D"),
    "EJS": ("ejs", "B4CA65"),
    "Vim Script": ("vim", "019733"),
    "Dockerfile": ("docker", "2496ED"),
}
PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    pullRequests { totalCount }
    merged: pullRequests(states: MERGED) { totalCount }
    open: pullRequests(states: OPEN) { totalCount }
  }
}
"""
LANGUAGE_FIELDS = """
  edges { size node { name } }
  pageInfo { hasNextPage endCursor }
"""
REPOSITORIES_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER,
                 privacy: PUBLIC, isFork: false,
                 orderBy: {field: NAME, direction: ASC}) {
      nodes {
        id
        languages(first: 100) { LANGUAGE_FIELDS }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".replace("LANGUAGE_FIELDS", LANGUAGE_FIELDS)
LANGUAGES_QUERY = """
query($id: ID!, $cursor: String!) {
  node(id: $id) {
    ... on Repository {
      languages(first: 100, after: $cursor) { LANGUAGE_FIELDS }
    }
  }
}
""".replace("LANGUAGE_FIELDS", LANGUAGE_FIELDS)


def graphql(query, **variables):
    args = ["gh", "api", "--hostname", "github.com", "graphql", "-f", f"query={query}"]
    for name, value in variables.items():
        if value is not None:
            args.extend(["-f", f"{name}={value}"])
    result = subprocess.run(args, capture_output=True, text=True, check=True, timeout=60)
    payload = json.loads(result.stdout)
    if payload.get("errors"):
        raise ValueError("GitHub returned GraphQL errors; the README was not updated.")
    return payload["data"]


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError(f"Invalid GitHub count: {value!r}")
    return value


def year_windows(created, now):
    if created > now:
        raise ValueError("Account creation date is after the report timestamp.")
    for year in range(created.year, now.year + 1):
        start = max(created, datetime(year, 1, 1, tzinfo=timezone.utc))
        # GitHub's date bounds are inclusive; don't count January 1 twice.
        end = min(now, datetime(year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1))
        yield year, start, end


def next_cursor(connection, seen):
    page = connection["pageInfo"]
    if type(page["hasNextPage"]) is not bool:
        raise ValueError("Invalid GitHub pagination metadata.")
    if not page["hasNextPage"]:
        return None
    cursor = page["endCursor"]
    if not isinstance(cursor, str) or not cursor or cursor in seen:
        raise ValueError("GitHub pagination did not advance; refusing a partial report.")
    seen.add(cursor)
    return cursor


def fetch_languages():
    totals = Counter()
    repositories = 0
    cursor, seen = None, set()
    while True:
        connection = graphql(REPOSITORIES_QUERY, login=USERNAME, cursor=cursor)["user"]["repositories"]
        for repository in connection["nodes"]:
            repositories += 1
            languages = repository["languages"]
            language_cursors = set()
            while True:
                for edge in languages["edges"]:
                    name = edge["node"]["name"]
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError("Invalid GitHub language name.")
                    totals[name] += count(edge["size"])
                language_cursor = next_cursor(languages, language_cursors)
                if language_cursor is None:
                    break
                languages = graphql(LANGUAGES_QUERY, id=repository["id"], cursor=language_cursor)["node"]["languages"]
        cursor = next_cursor(connection, seen)
        if cursor is None:
            return repositories, {name: size for name, size in totals.items() if size}


def fetch_profile(now):
    user = graphql(PROFILE_QUERY, login=USERNAME)["user"]
    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    if created.utcoffset() != timedelta(0):
        raise ValueError("Expected a UTC account creation date.")
    prs, merged, opened = (count(user[field]["totalCount"]) for field in ("pullRequests", "merged", "open"))
    if merged + opened > prs:
        raise ValueError("Inconsistent pull request totals from GitHub.")
    windows = list(year_windows(created, now))
    fields = "\n".join(
        f'y{year}: contributionsCollection(from: "{start.isoformat()}", to: "{end.isoformat()}") '
        "{ totalCommitContributions restrictedContributionsCount }"
        for year, start, end in windows
    )
    history = graphql("query($login: String!) { user(login: $login) {" + fields + "} }", login=USERNAME)["user"]
    years = [(year, count(history[f"y{year}"]["totalCommitContributions"])) for year, _, _ in windows]
    restricted = sum(count(history[f"y{year}"]["restrictedContributionsCount"]) for year, _, _ in windows)
    repositories, languages = fetch_languages()
    return {
        "created": created, "years": years, "restricted": restricted,
        "prs": prs, "merged": merged, "open": opened,
        "repositories": repositories, "languages": languages,
    }


def language_badge(name, share):
    # Keep API labels from injecting Markdown; urlencode handles badge parameters.
    label = "".join(char if char.isalnum() or char in " +#.-()" else "?" for char in name)
    logo, color = LANGUAGE_LOGOS.get(name, (None, "555555"))
    params = {
        "label": label, "message": share, "color": color,
        "style": "flat-square", "labelColor": "1f2328",
    }
    if logo:
        params.update(logo=logo, logoColor="white")
    url = "https://img.shields.io/static/v1?" + urlencode(params)
    return f"![{label} {share}]({url})"


def render_report(profile, now):
    commits = sum(value for _, value in profile["years"])
    lines = [
        "### On GitHub",
        "",
        f"Since {profile['created']:%B %Y}",
        "",
        "<table>",
        "  <tr>",
        '    <td align="center" valign="top" width="280">',
        f"      <h3>{commits + profile['restricted']:,}</h3>",
        f'      <sub title="Combines {commits:,} commit contributions and '
        f'{profile["restricted"]:,} restricted contributions, which can include non-commit activity.">'
        "TOTAL&nbsp;COMMITS</sub>",
        "    </td>",
        '    <td align="center" valign="top" width="280">',
        f"      <h3>{profile['prs']:,}</h3>",
        "      <sub>PRS&nbsp;OPENED</sub>",
        "    </td>",
        '    <td align="center" valign="top" width="280">',
        f"      <h3>{profile['merged']:,}</h3>",
        "      <sub>PRS&nbsp;MERGED</sub>",
        "    </td>",
        "  </tr>",
        "</table>",
        "",
        "### Languages",
        "",
    ]
    languages = sorted(profile["languages"].items(), key=lambda item: (-item[1], item[0]))
    total = sum(size for _, size in languages)
    if total:
        badges = []
        for name, size in languages:
            share = "<0.1%" if size / total < 0.001 else f"{size / total:.1%}"
            badges.append(language_badge(name, share))
        for start in range(0, len(badges), 5):
            row = badges[start:start + 5]
            lines.append("| " + " | ".join(row + [""] * (5 - len(row))) + " |")
            if start == 0:
                lines.append("| " + " | ".join([":---"] * 5) + " |")
    else:
        lines.append("No language data available.")
    lines.extend(["", f"_Updated {now:%Y-%m-%d %H:%M:%S} UTC_", ""])
    return "\n".join(lines)


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
        raise ValueError("Switch to main before publishing the profile report.")
    if git("status", "--porcelain"):
        raise ValueError("Working tree must be clean. Commit or stash your changes first.")
    git("remote", "get-url", "--push", "origin")
    path = ROOT / "README.md"
    original = path.read_text(encoding="utf-8")
    replace_report(original, "")  # Validate markers before requesting GitHub data.
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = render_report(fetch_profile(now), now)
    updated = replace_report(original, report)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        git("add", "--", "README.md")
        git("commit", "-m", "chore: refresh profile stats", "--", "README.md")
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
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired) as error:
        print(f"Could not publish report: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
