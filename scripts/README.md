# Weekly report

Updates the marked section in `README.md` with the last seven complete UTC days
of GitHub activity, commits it, and pushes **all unpushed commits** to `origin/main`.
No GitHub Actions; existing SVGs are not refreshed.

## Setup

Requires Python 3, Git, and [GitHub CLI](https://cli.github.com/).
Configure your Git commit identity and SSH access to `origin`, then authenticate:

```sh
gh auth login --hostname github.com
```

## Run weekly

Commit any setup changes first. Run on `main` with a clean working tree:

```sh
python3 scripts/weekly_report.py
```

- Publishes aggregate counts, potentially including private contributions—not repository names.
- Unchanged reports skip the commit but still push. No force-push or automatic pull.
- Failed push? Fix the cause and rerun; local commits are kept.
- Failed commit? Resolve and commit the staged README manually before rerunning.

## Tests (offline)

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
```
