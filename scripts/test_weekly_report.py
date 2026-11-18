import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import weekly_report as report


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2024, 2, 23)
        self.end = date(2024, 2, 29)
        self.counts = {field: index for index, (_, field) in enumerate(report.METRICS)}

    def test_period_excludes_today_and_crosses_leap_day(self):
        self.assertEqual(
            report.report_period(date(2024, 3, 1)), (self.start, self.end)
        )

    @patch("weekly_report.subprocess.run")
    def test_fetch_passes_utc_boundaries(self, run):
        run.return_value.stdout = json.dumps({
            "data": {"user": {"contributionsCollection": self.counts}}
        })
        self.assertEqual(report.fetch_contributions(self.start, self.end), self.counts)
        args = run.call_args.args[0]
        self.assertIn("from=2024-02-23T00:00:00Z", args)
        self.assertIn("to=2024-02-29T23:59:59Z", args)
        self.assertIn("login=durgeshbg", args)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_render_includes_zero_counts_and_all_metrics(self):
        markdown = report.render_report(self.counts, self.start, self.end)
        self.assertIn("2024-02-23 – 2024-02-29 (UTC)", markdown)
        for index, (label, _) in enumerate(report.METRICS):
            self.assertIn(f"| {label} | {index} |", markdown)

    def test_rejects_invalid_counts(self):
        for invalid in (-1, None, True, "3"):
            with self.subTest(invalid=invalid):
                self.counts["totalCommitContributions"] = invalid
                with self.assertRaises(ValueError):
                    report.render_report(self.counts, self.start, self.end)

    @patch("weekly_report.subprocess.run")
    def test_graphql_errors_are_not_treated_as_zero_activity(self, run):
        run.return_value.stdout = json.dumps({"errors": [{"message": "denied"}]})
        with self.assertRaisesRegex(ValueError, "GraphQL errors"):
            report.fetch_contributions(self.start, self.end)

    @patch("weekly_report.subprocess.run")
    def test_invalid_responses_are_rejected(self, run):
        for payload in ("not json", '{"data": {"user": null}}',
                        '{"data": {"user": {"contributionsCollection": {}}}}'):
            with self.subTest(payload=payload):
                run.return_value.stdout = payload
                with self.assertRaises((ValueError, TypeError, KeyError)):
                    report.render_report(
                        report.fetch_contributions(self.start, self.end), self.start, self.end
                    )

    @patch("weekly_report.publish_report")
    def test_failures_return_nonzero(self, publish):
        for error in (FileNotFoundError("gh missing"), ValueError("Invalid response"),
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
        fetch = patch.object(report, "fetch_contributions", return_value={
            field: 2 for _, field in report.METRICS
        })
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def publish(self):
        with redirect_stdout(io.StringIO()):
            report.publish_report()

    def test_updates_only_section_commits_pushes_and_skips_duplicate_commit(self):
        self.publish()
        updated = self.path.read_text()
        self.assertTrue(updated.startswith("Keep this intro.\n" + report.START_MARKER))
        self.assertTrue(updated.endswith(report.END_MARKER + "\nKeep this footer.\n"))
        self.assertIn("| Commits | 2 |", updated)
        head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(head, self.initial)
        self.assertEqual(self.git("--git-dir", str(self.remote), "rev-parse", "main"), head)
        self.assertEqual(self.git("show", "--pretty=format:", "--name-only", "HEAD"), "README.md")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.publish()
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

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

    def test_failed_push_keeps_commit_and_rerun_pushes_without_duplicate(self):
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        # Isolate the bare remote's hook setting from the user's global config.
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
