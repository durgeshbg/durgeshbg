# Profile report

A compact profile with three number tiles, a five-column language-logo grid,
and a UTC timestamp. Your bio and links stay outside the generated section.
Stats come from GitHub; static SVG badges are hosted by [Shields.io](https://shields.io/).
No Actions or extra packages. Missing logos fall back to name-and-percentage badges.
Badge URLs contain public language names/shares only, never credentials or private data.

## Setup

Requires Python 3, Git, [GitHub CLI](https://cli.github.com/), a Git commit identity,
and push access to `origin`. Use your existing login or authenticate:

```sh
gh auth login --hostname github.com
```

## Refresh whenever you like

Commit setup changes first; use `main` with a clean working tree:

```sh
python3 scripts/weekly_report.py
```

- Re-fetches account history, updates the README and UTC timestamp, commits,
  then pushes **all unpushed commits** to `origin/main`. No schedule required.
- The custom “Total commits” tile combines counted commits and restricted activity.
  Its tooltip explains that this includes non-commit activity, not a verified commit-only total.
  No private names/details are published, and no extra token scopes are required.
- Commits follow [GitHub's contribution rules](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference).
  Languages measure current public, owned, non-fork code bytes, not personal authorship.
- Failed push: fix the cause and rerun; local commits stay. No force-push or auto-pull.
  Failed commit: resolve and commit the staged README manually before rerunning.

## Tests (offline, temporary Git repositories)

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
```
