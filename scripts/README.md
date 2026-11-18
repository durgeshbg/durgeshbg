# Profile report

An ASCII dashboard of lifetime commit contributions, PRs opened/merged, yearly
history, and public-repository language bytes. Fetches directly from GitHub;
no card services or Actions. The local snake SVG remains an archived snapshot.

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
- Private activity shows GitHub's restricted-contribution count separately, never
  names or details. It is not a complete private-work total and is not added to commits.
  No extra token scopes are required just to display this count.
- Commits follow [GitHub's contribution rules](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference).
  Languages measure current public, owned, non-fork code bytes, not personal authorship.
- Failed push: fix the cause and rerun; local commits stay. No force-push or auto-pull.
  Failed commit: resolve and commit the staged README manually before rerunning.

## Tests (offline, temporary Git repositories)

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
```
