import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import weekly_report as report

NOW = datetime(2024, 3, 1, 12, 30, tzinfo=timezone.utc)


def profile():
    return {
        "created": datetime(2021, 5, 27, 11, 16, 55, tzinfo=timezone.utc),
        "years": [(2021, 38), (2022, 637), (2023, 675), (2024, 100)],
        "restricted": 12, "prs": 547, "merged": 506, "open": 21,
        "repositories": 2, "languages": {"Python": 750, "TypeScript": 250},
    }


def connection(key, items, cursor=None):
    return {key: items, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def language(name, size):
    return {"size": size, "node": {"name": name}}


class ProfileReportTests(unittest.TestCase):
    def test_year_windows_cover_history_without_overlapping_inclusive_bounds(self):
        created = profile()["created"]
        windows = list(report.year_windows(created, NOW))
        self.assertEqual([year for year, _, _ in windows], [2021, 2022, 2023, 2024])
        self.assertEqual(windows[0][1], created)
        self.assertEqual(windows[-1][2], NOW)
        for previous, current in zip(windows, windows[1:]):
            self.assertEqual(previous[2] + timedelta(seconds=1), current[1])
        leap_end = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        leap = list(report.year_windows(datetime(2024, 1, 1, tzinfo=timezone.utc), leap_end))
        self.assertEqual((leap[0][2] - leap[0][1]).days, 365)
        self.assertEqual(list(report.year_windows(NOW, NOW)), [(2024, NOW, NOW)])
        with self.assertRaises(ValueError):
            list(report.year_windows(NOW + timedelta(seconds=1), NOW))

    @patch("weekly_report.subprocess.run")
    def test_graphql_passes_variables_and_rejects_partial_errors(self, run):
        run.return_value.stdout = json.dumps({"data": {"user": {"ok": True}}})
        self.assertEqual(report.graphql("query", login="durgeshbg", cursor=None), {"user": {"ok": True}})
        args = run.call_args.args[0]
        self.assertIn("login=durgeshbg", args)
        self.assertNotIn("cursor=None", args)
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        for response in ("not json", '{"errors": ["denied"], "data": {}}', '{}'):
            run.return_value.stdout = response
            with self.assertRaises((ValueError, KeyError)):
                report.graphql("query")

    def test_counts_reject_negative_boolean_and_missing_values(self):
        for value in (-1, True, None, "3", 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                report.count(value)
        self.assertEqual(report.count(0), 0)

    @patch("weekly_report.fetch_languages", return_value=(2, {"Python": 42}))
    @patch("weekly_report.graphql")
    def test_fetch_uses_lifetime_totals_and_never_calls_restricted_activity_commits(self, graphql, languages):
        graphql.side_effect = [
            {"user": {"createdAt": "2021-05-27T11:16:55Z", "pullRequests": {"totalCount": 547},
                      "merged": {"totalCount": 506}, "open": {"totalCount": 21}}},
            {"user": {f"y{year}": {"totalCommitContributions": 10, "restrictedContributionsCount": 3}
                      for year in range(2021, 2025)}},
        ]
        result = report.fetch_profile(NOW)
        self.assertEqual(sum(value for _, value in result["years"]), 40)
        self.assertEqual(result["restricted"], 12)
        self.assertEqual(result["merged"], 506)
        self.assertIn('from: "2021-05-27T11:16:55+00:00"', graphql.call_args.args[0])
        self.assertIn('to: "2024-03-01T12:30:00+00:00"', graphql.call_args.args[0])

    @patch("weekly_report.graphql")
    def test_inconsistent_or_missing_profile_data_fails(self, graphql):
        user = {"createdAt": "2024-01-01T00:00:00Z", "pullRequests": {"totalCount": 1},
                "merged": {"totalCount": 1}, "open": {"totalCount": 1}}
        graphql.return_value = {"user": user}
        with self.assertRaisesRegex(ValueError, "Inconsistent"):
            report.fetch_profile(NOW)
        graphql.return_value = {"user": None}
        with self.assertRaises(TypeError):
            report.fetch_profile(NOW)

    @patch("weekly_report.graphql")
    def test_paginates_repositories_and_each_language_connection(self, graphql):
        graphql.side_effect = [
            {"user": {"repositories": connection("nodes", [
                {"id": "r1", "languages": connection("edges", [language("Python", 30)], "lang-next")}
            ], "repo-next")}},
            {"node": {"languages": connection("edges", [language("JavaScript", 10)])}},
            {"user": {"repositories": connection("nodes", [
                {"id": "r2", "languages": connection("edges", [language("Python", 20)])},
                {"id": "r3", "languages": connection("edges", [])},
            ])}},
        ]
        self.assertEqual(report.fetch_languages(), (3, {"Python": 50, "JavaScript": 10}))
        self.assertEqual(graphql.call_args_list[1].kwargs, {"id": "r1", "cursor": "lang-next"})
        self.assertEqual(graphql.call_args_list[2].kwargs, {"login": "durgeshbg", "cursor": "repo-next"})
        self.assertIn("privacy: PUBLIC", report.REPOSITORIES_QUERY)
        self.assertIn("isFork: false", report.REPOSITORIES_QUERY)
        self.assertIn("ownerAffiliations: OWNER", report.REPOSITORIES_QUERY)

    def test_invalid_or_repeating_pagination_fails_instead_of_truncating(self):
        seen = set()
        page = connection("nodes", [], "same")
        self.assertEqual(report.next_cursor(page, seen), "same")
        with self.assertRaises(ValueError):
            report.next_cursor(page, seen)
        for page_info in ({"hasNextPage": True, "endCursor": None}, {"hasNextPage": "false"}):
            with self.assertRaises(ValueError):
                report.next_cursor({"pageInfo": page_info}, set())

    def test_render_is_ascii_aligned_and_has_lifetime_totals_and_timestamp(self):
        rendered = report.render_report(profile(), NOW)
        panel = rendered.split("```text\n")[1].split("\n```")[0]
        self.assertTrue(panel.isascii())
        self.assertEqual(len({len(line) for line in panel.splitlines()}), 1)
        for text in ("1,450", "547", "506", "92.5%", "CLOSED, UNMERGED  20",
                     "2024-03-01 12:30:00 UTC", "75.0%", "25.0%", "PRIVATE ACTIVITY"):
            self.assertIn(text, rendered)
        totals = next(line for line in panel.splitlines() if "1,450" in line)
        self.assertEqual(totals.strip("| ").split(), ["1,450", "547", "506", "12"])
        self.assertNotIn("1,462", rendered)
        self.assertNotIn("Partial:", rendered)
        self.assertNotIn("Incomplete commit coverage", rendered)
        self.assertIn("not a total of all private work or private commits", rendered)
        self.assertNotIn("<img", rendered)

    def test_bars_show_small_nonzero_values_without_overflow(self):
        self.assertEqual(report.bar(0, 0), "." * 22)
        self.assertEqual(report.bar(1, 1000), "+" + "." * 21)
        self.assertEqual(report.bar(1000, 1000), "#" * 22)
        self.assertEqual(len(report.bar(574, 1000)), 22)

    def test_empty_profile_and_zero_denominators(self):
        empty = profile()
        empty.update(years=[(2024, 0)], prs=0, merged=0, open=0, restricted=0, repositories=0, languages={})
        rendered = report.render_report(empty, NOW)
        self.assertIn("n/a", rendered)
        self.assertIn("No language data available.", rendered)
        self.assertIn("PRIVATE ACTIVITY", rendered)
        panel = rendered.split("```text\n")[1].split("\n```")[0]
        self.assertTrue(any(line.strip("| ").split() == ["0", "0", "0", "0"] for line in panel.splitlines()))
        self.assertNotIn("Incomplete commit coverage", rendered)

    def test_language_tail_is_grouped_without_changing_percentage_denominator(self):
        data = profile()
        data["languages"] = {f"Language {index}": 10 for index in range(10)}
        rendered = report.render_report(data, NOW)
        self.assertEqual(rendered.count("10.0%"), 8)
        self.assertIn("20.0%", rendered)
        self.assertIn("Other", rendered)
        self.assertIn("10 languages detected", rendered)

    def test_large_counts_and_untrusted_language_labels_preserve_panel(self):
        data = profile()
        data.update(prs=12345678, languages={"bad\n```|é" * 5: 1})
        rendered = report.render_report(data, NOW)
        self.assertEqual(rendered.count("```"), 2)
        panel = rendered.split("```text\n")[1].split("\n```")[0]
        self.assertTrue(panel.isascii())
        self.assertEqual(len({len(line) for line in panel.splitlines()}), 1)

    @patch("weekly_report.publish_report")
    def test_failures_return_nonzero(self, publish):
        for error in (FileNotFoundError("gh missing"), ValueError("Invalid response"),
                      subprocess.TimeoutExpired("gh", 60),
                      subprocess.CalledProcessError(1, ["git"], stderr="Push rejected")):
            with self.subTest(error=error):
                publish.side_effect = error
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    self.assertEqual(report.main(), 1)
                self.assertTrue(stderr.getvalue())

    def test_markers_must_be_unique_and_ordered(self):
        for text in ("no markers", report.END_MARKER + report.START_MARKER,
                     report.START_MARKER * 2 + report.END_MARKER):
            with self.subTest(text=text), self.assertRaises(ValueError):
                report.replace_report(text, "new report")


class PublishingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        self.remote = base / "remote.git"
        root_patch = patch.object(report, "ROOT", self.repo)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        clock = patch.object(report, "datetime", wraps=datetime)
        self.clock = clock.start()
        self.clock.now.return_value = NOW
        self.addCleanup(clock.stop)
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(base / "no-hooks"))
        self.git("init", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.path = self.repo / "README.md"
        self.original = ("Keep this intro.\n" + report.START_MARKER +
                         "\nOld report\n" + report.END_MARKER + "\nKeep this footer.\n")
        self.path.write_text(self.original)
        self.git("add", "README.md")
        self.git("commit", "-m", "Initial README")
        self.git("push", "origin", "main")
        self.initial = self.git("rev-parse", "HEAD")
        fetch = patch.object(report, "fetch_profile", return_value=profile())
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def publish(self):
        with redirect_stdout(io.StringIO()):
            report.publish_report()

    def test_updates_only_section_commits_pushes_and_skips_identical_snapshot(self):
        self.publish()
        updated = self.path.read_text()
        self.assertTrue(updated.startswith("Keep this intro.\n" + report.START_MARKER))
        self.assertTrue(updated.endswith(report.END_MARKER + "\nKeep this footer.\n"))
        self.assertIn("1,450", updated)
        head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(head, self.initial)
        self.assertEqual(self.git("--git-dir", str(self.remote), "rev-parse", "main"), head)
        self.assertEqual(self.git("show", "--pretty=format:", "--name-only", "HEAD"), "README.md")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.publish()
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.clock.now.return_value = NOW + timedelta(days=20)
        self.publish()
        self.assertNotEqual(self.git("rev-parse", "HEAD"), head)
        self.assertIn("2024-03-21 12:30:00 UTC", self.path.read_text())

    def test_refuses_unstaged_staged_and_untracked_changes(self):
        for state in ("unstaged", "staged", "untracked"):
            with self.subTest(state=state):
                if state == "untracked":
                    (self.repo / "other.txt").write_text("unrelated")
                else:
                    self.path.write_text(self.original + "manual change\n")
                    if state == "staged":
                        self.git("add", "README.md")
                with self.assertRaisesRegex(ValueError, "clean"):
                    self.publish()
                self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
                self.git("reset", "--hard", "HEAD")
        self.fetch.assert_not_called()

    def test_refuses_other_branches_and_detached_head(self):
        for args in (("checkout", "-b", "other"), ("checkout", "--detach")):
            self.git(*args)
            with self.assertRaisesRegex(ValueError, "main"):
                self.publish()
        self.fetch.assert_not_called()

    def test_bad_markers_and_api_failure_leave_readme_unchanged(self):
        self.fetch.side_effect = ValueError("API failure")
        with self.assertRaisesRegex(ValueError, "API failure"):
            self.publish()
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.path.write_text("Missing markers\n")
        self.git("add", "README.md")
        self.git("commit", "-m", "Remove markers")
        self.fetch.reset_mock()
        with self.assertRaisesRegex(ValueError, "markers"):
            self.publish()
        self.fetch.assert_not_called()
        self.assertEqual(self.path.read_text(), "Missing markers\n")

    def test_commit_failure_keeps_staged_readme_without_pushing(self):
        hooks = self.repo / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.git("config", "core.hooksPath", str(hooks))
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.git("--git-dir", str(self.remote), "rev-parse", "main"), self.initial)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "README.md")
        self.assertIn("UPDATED AT", self.path.read_text())

    def test_remote_divergence_is_not_force_pushed(self):
        remote_head = self.git("commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "Remote change")
        self.git("push", "origin", f"{remote_head}:refs/heads/main")
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(self.git("--git-dir", str(self.remote), "rev-parse", "main"), remote_head)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_push_includes_pending_setup_commit(self):
        (self.repo / "setup.txt").write_text("Setup changes")
        self.git("add", "setup.txt")
        self.git("commit", "-m", "Set up report")
        self.publish()
        self.assertEqual(self.git("--git-dir", str(self.remote), "rev-list", "--count", "main"), "3")
        self.assertEqual(self.git("--git-dir", str(self.remote), "show", "main:setup.txt"), "Setup changes")

    def test_failed_push_keeps_commit_and_rerun_pushes_without_duplicate(self):
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.git("--git-dir", str(self.remote), "config", "core.hooksPath", str(hook.parent))
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(head, self.initial)
        self.assertEqual(self.git("status", "--porcelain"), "")
        hook.unlink()
        self.publish()
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("--git-dir", str(self.remote), "rev-parse", "main"), head)


if __name__ == "__main__":
    unittest.main()
