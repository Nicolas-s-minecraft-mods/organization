"""Tests of the synchronization of the project: python3 -m unittest discover -s scripts -p 'test_*.py'."""
import io
import unittest
import urllib.error

import project_sync

ORG = "Org"
DATE_FIELDS = {"Start date": 2, "Target date": 3}


class FakeGitHub:
    """A project and repositories in memory."""

    def __init__(self, repos, items=None, fields=None, missing=(), issue_fields=DATE_FIELDS, values=None):
        self.repos = repos
        self.items_ = dict(items or {})
        self.fields = fields if fields is not None else {"Estimate": {"id": "F1", "dataType": "NUMBER"}}
        self.missing = set(missing)
        self.added = []
        self.numbers = []
        self.dates = []
        self.issue_fields_ = issue_fields
        self.values = dict(values or {})
        self.links = []
        self.blockers = {}

    def project(self, org, number):
        return {"id": "P1", "fields": self.fields}

    def items(self, project_id, field):
        return {key: dict(value) for key, value in self.items_.items()}

    def repositories(self, org):
        return list(self.repos)

    def open_issues(self, owner, repo):
        return self.repos[repo]

    def issue(self, owner, repo, number):
        if (repo, number) in self.missing:
            raise urllib.error.HTTPError("url", 404, "Not Found", {}, io.BytesIO(b""))
        return {"id": hash((repo.lower(), number)) & 0xFFFFFF}

    def add_item(self, project_id, content_id):
        self.added.append(content_id)
        return f"I-{content_id}"

    def set_number(self, project_id, item_id, field_id, value):
        self.numbers.append((item_id, field_id, value))

    def issue_fields(self, org):
        if self.issue_fields_ is None:
            raise urllib.error.HTTPError("url", 403, "Forbidden", {}, io.BytesIO(b""))
        return dict(self.issue_fields_)

    def field_values(self, owner, repo, number):
        return dict(self.values.get((repo, number), {}))

    def set_field_values(self, owner, repo, number, values):
        self.dates.append((repo, number, dict(values)))

    def blocked_by(self, owner, repo, number):
        return set(self.blockers.get((repo, number), set()))

    def add_blocked_by(self, owner, repo, number, blocking_id):
        self.links.append((repo, number, blocking_id))


def issue(number, body="", node=None, title="Title"):
    return {"number": number, "node_id": node or f"N{number}", "title": title, "body": body}


def run(api, dry_run=False):
    lines = []
    sync = project_sync.Sync(api, ORG, 2, dry_run=dry_run, out=lines.append)
    return sync.run(), lines


class ParseTest(unittest.TestCase):
    def test_estimate_accepts_lists_bold_and_units(self):
        self.assertEqual(6.0, project_sync.estimate("Text\nEstimate: 6"))
        self.assertEqual(6.0, project_sync.estimate("- **Estimate**: 6h"))
        self.assertEqual(1.5, project_sync.estimate("**Estimate:** 1,5 hours"))
        self.assertEqual(12.0, project_sync.estimate("* estimate : 12 h"))

    def test_estimate_needs_its_own_line(self):
        self.assertIsNone(project_sync.estimate("The estimate: 6h is in a sentence."))
        self.assertIsNone(project_sync.estimate(None))
        self.assertIsNone(project_sync.estimate("Estimate: unknown"))

    def test_dates_accept_lists_and_bold(self):
        self.assertEqual({"start": "2026-11-23", "target": "2026-11-25"},
                         project_sync.dates("Estimate: 2h\nStart: 2026-11-23\n- **Target**: 2026-11-25"))

    def test_dates_need_their_own_line_and_a_real_date(self):
        self.assertEqual({"start": None, "target": None},
                         project_sync.dates("The start: 2026-11-23 is in a sentence.\nTarget: 2026-02-30"))
        self.assertEqual({"start": None, "target": None}, project_sync.dates(None))

    def test_the_first_date_line_counts(self):
        self.assertEqual("2026-11-23", project_sync.dates("Start: 2026-11-23\nStart: 2026-12-01")["start"])

    def test_references_of_each_form(self):
        refs = project_sync.references(
            "#12, Folkhold#597, other/repo#3 and https://github.com/a/b/issues/9, #12 again", ORG, "Cartofold")

        self.assertEqual([(ORG, "Cartofold", 12), (ORG, "Folkhold", 597), ("other", "repo", 3), ("a", "b", 9)], refs)

    def test_dependencies_split_blocked_by_and_blocks(self):
        body = "## Dependencies\n- **Blocked by**: #4\n- Blocks: Folkhold#597, Folkhold#882\nBlocks nothing #7"

        blocked_by, blocks = project_sync.dependencies(body, ORG, "Cartofold")

        self.assertEqual([(ORG, "Cartofold", 4)], blocked_by)
        self.assertEqual([(ORG, "Folkhold", 597), (ORG, "Folkhold", 882)], blocks)


class SyncTest(unittest.TestCase):
    def test_adds_the_missing_issues_and_sets_the_estimates(self):
        api = FakeGitHub({"Cartofold": [issue(1, "Estimate: 5h"), issue(2)]},
                         items={"N2": {"item": "I2", "estimate": None}})

        errors, lines = run(api)

        self.assertEqual(0, errors)
        self.assertEqual(["N1"], api.added)
        self.assertEqual([("I-N1", "F1", 5.0)], api.numbers)
        self.assertIn("Added: Cartofold#1 Title", lines)

    def test_keeps_an_estimate_that_did_not_change(self):
        api = FakeGitHub({"Cartofold": [issue(1, "Estimate: 5")]}, items={"N1": {"item": "I1", "estimate": 5.0}})

        errors, _ = run(api)

        self.assertEqual(0, errors)
        self.assertEqual([], api.added)
        self.assertEqual([], api.numbers)

    def test_links_the_two_directions_once(self):
        cartofold = [issue(123, "Blocks: Folkhold#597"), issue(124, "Blocked by: #123, #124")]
        folkhold = [issue(597, "Blocked by: Cartofold#123", node="F597")]
        api = FakeGitHub({"Cartofold": cartofold, "Folkhold": folkhold})

        errors, _ = run(api)

        blocker = api.issue(ORG, "Cartofold", 123)["id"]
        self.assertEqual(0, errors)
        self.assertEqual([("Folkhold", 597, blocker), ("Cartofold", 124, blocker)], api.links)

    def test_an_existing_dependency_is_not_added_again(self):
        api = FakeGitHub({"Cartofold": [issue(2, "Blocked by: #1")]})
        api.blockers[("Cartofold", 2)] = {api.issue(ORG, "Cartofold", 1)["id"]}

        errors, _ = run(api)

        self.assertEqual(0, errors)
        self.assertEqual([], api.links)

    def test_an_unknown_issue_is_a_warning_and_the_sync_continues(self):
        api = FakeGitHub({"Cartofold": [issue(1, "Blocked by: Folkhold#9\nEstimate: 2"), issue(2, "Estimate: 3")]},
                         missing={("Folkhold", 9)})

        errors, lines = run(api)

        self.assertEqual(1, errors)
        self.assertEqual(2, len(api.numbers))
        self.assertTrue(any(line.startswith("::warning::No dependency") for line in lines))

    def test_dry_run_changes_nothing(self):
        api = FakeGitHub({"Cartofold": [issue(1, "Estimate: 4\nBlocked by: #2"), issue(2)]})

        errors, lines = run(api, dry_run=True)

        self.assertEqual(0, errors)
        self.assertEqual(([], [], [], []), (api.added, api.numbers, api.dates, api.links))
        self.assertIn("Estimate: Cartofold#1 4 h.", lines)
        self.assertIn("Dependency: Org/Cartofold#1 blocked by Org/Cartofold#2.", lines)

    def test_no_estimate_field_is_a_warning(self):
        api = FakeGitHub({"Cartofold": [issue(1, "Estimate: 4")]}, fields={})

        errors, _ = run(api)

        self.assertEqual(1, errors)
        self.assertEqual(["N1"], api.added)
        self.assertEqual([], api.numbers)

    def test_sets_the_dates_that_changed(self):
        issues = [issue(1, "Start: 2026-11-23\nTarget: 2026-11-25"), issue(2, "Start: 2026-11-26"), issue(3, "Text")]
        api = FakeGitHub({"Folkhold": issues}, values={("Folkhold", 1): {2: "2026-11-23", 3: "2026-11-24"}})

        errors, lines = run(api)

        self.assertEqual(0, errors)
        self.assertEqual([("Folkhold", 1, {3: "2026-11-25"}), ("Folkhold", 2, {2: "2026-11-26"})], api.dates)
        self.assertIn("Target: Folkhold#1 2026-11-25.", lines)
        self.assertNotIn("Start: Folkhold#1 2026-11-23.", lines)

    def test_dry_run_sets_no_date(self):
        api = FakeGitHub({"Folkhold": [issue(1, "Start: 2026-11-23")]})

        errors, lines = run(api, dry_run=True)

        self.assertEqual(0, errors)
        self.assertEqual([], api.dates)
        self.assertIn("Start: Folkhold#1 2026-11-23.", lines)

    def test_no_date_field_is_a_warning_and_the_estimates_continue(self):
        api = FakeGitHub({"Folkhold": [issue(1, "Estimate: 4\nStart: 2026-11-23\nTarget: 2026-11-25")]},
                         issue_fields={"Start date": 2})

        errors, _ = run(api)

        self.assertEqual(1, errors)
        self.assertEqual([("I-N1", "F1", 4.0)], api.numbers)
        self.assertEqual([("Folkhold", 1, {2: "2026-11-23"})], api.dates)

    def test_field_ids_need_no_read_of_the_issue_fields(self):
        api = FakeGitHub({"Folkhold": [issue(1, "Start: 2026-11-23\nTarget: 2026-11-25")]}, issue_fields=None)
        lines = []
        sync = project_sync.Sync(api, ORG, 2, out=lines.append, start_field_id=7, target_field_id=8)

        self.assertEqual(0, sync.run())
        self.assertEqual([("Folkhold", 1, {7: "2026-11-23", 8: "2026-11-25"})], api.dates)

    def test_no_access_to_the_issue_fields_is_a_warning(self):
        api = FakeGitHub({"Folkhold": [issue(1, "Estimate: 4\nStart: 2026-11-23")]}, issue_fields=None)

        errors, lines = run(api)

        self.assertEqual(3, errors)
        self.assertEqual([("I-N1", "F1", 4.0)], api.numbers)
        self.assertEqual([], api.dates)


if __name__ == "__main__":
    unittest.main()
