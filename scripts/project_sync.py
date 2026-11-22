#!/usr/bin/env python3
"""Synchronize the open issues of an organization with a GitHub project.

    PROJECT_TOKEN=... python3 scripts/project_sync.py --org Nicolas-s-minecraft-mods --project 2
    PROJECT_TOKEN=... python3 scripts/project_sync.py --org Nicolas-s-minecraft-mods --project 2 --dry-run

For each open issue of each repository of the organization (archived repositories excluded), the script:

- adds the issue to the project when the project does not have it;
- sets the number field of the estimate (default "Estimate") from the line `Estimate: <hours>` of the issue;
- sets the date issue fields of the organization (default "Start date" and "Target date") from the lines
  `Start: <date>` and `Target: <date>` of the issue;
- adds the dependencies of the lines `Blocked by: <issues>` and `Blocks: <issues>` of the issue.

The lines can be in a list or in bold, for example `- **Estimate**: 6h` or `**Blocked by**: Folkhold#597`. The
estimate is a number of hours, with or without `h`. A date has the format `2026-11-23`. An issue is `#12` (same
repository), `Repo#12` (same organization), `owner/repo#12` or the URL of the issue. The script only adds or changes
values: it does not remove an item, an estimate, a date or a dependency. Remove them in the project or in the issue.

The token needs the organization permission "Projects" (read and write), and the repository permissions "Issues" (read
and write) and "Metadata" (read) on all the repositories of the organization. With the organization permission
"Issue Fields" (read), the script finds the date fields from their names. With no such permission, give their IDs with
`--start-field-id` and `--target-field-id`. A fine-grained personal access token of
the organization, or a classic token with the scopes `project` and `repo`, gives these permissions.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
API_VERSION = "2022-11-28"
FIELDS_API_VERSION = "2026-03-10"
USER_AGENT = "Nicolas-s-minecraft-mods/organization (project workflow)"

_PREFIX = r"^[ \t]*(?:[-*+][ \t]+)?(?:\*\*|__)?"
_SUFFIX = r"(?:\*\*|__)?[ \t]*:(?:\*\*|__)?[ \t]*"
ESTIMATE = re.compile(_PREFIX + r"estimate" + _SUFFIX + r"(\d+(?:[.,]\d+)?)[ \t]*(?:h|hours?)?\b",
                      re.IGNORECASE | re.MULTILINE)
DATE = re.compile(_PREFIX + r"(start|target)" + _SUFFIX + r"(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE | re.MULTILINE)
DEPENDENCY = re.compile(_PREFIX + r"(blocked[ \t]+by|blocks)" + _SUFFIX + r"(.+)$", re.IGNORECASE | re.MULTILINE)
REFERENCE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)"
                       r"|(?<![\w/.-])(?:(?:([\w.-]+)/)?([\w.-]+))?#(\d+)")


def estimate(body):
    """The estimate of an issue body in hours, or None. The first line `Estimate:` counts."""
    match = ESTIMATE.search(body or "")
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def dates(body):
    """The dates of an issue body: {"start": date or None, "target": date or None}. The first line of each counts."""
    found = {"start": None, "target": None}
    for match in DATE.finditer(body or ""):
        key = match.group(1).lower()
        if found[key] is None:
            try:
                found[key] = datetime.date.fromisoformat(match.group(2)).isoformat()
            except ValueError:
                pass
    return found


def references(text, owner, repo):
    """The issues of a text, as (owner, repository, number) tuples, in order and with no duplicates."""
    found = []
    for match in REFERENCE.finditer(text):
        if match.group(1):
            ref = (match.group(1), match.group(2), int(match.group(3)))
        else:
            ref_owner = match.group(4) or owner
            ref_repo = match.group(5) or repo
            ref = (ref_owner, ref_repo, int(match.group(6)))
        if ref not in found:
            found.append(ref)
    return found


def dependencies(body, owner, repo):
    """The issues that block this issue and the issues that this issue blocks, as two lists of references."""
    blocked_by, blocks = [], []
    for match in DEPENDENCY.finditer(body or ""):
        target = blocked_by if match.group(1).lower().startswith("blocked") else blocks
        for ref in references(match.group(2), owner, repo):
            if ref not in target:
                target.append(ref)
    return blocked_by, blocks


def same_issue(first, second):
    """True if two references give the same issue. The names of GitHub are not case-sensitive."""
    return first[0].lower() == second[0].lower() and first[1].lower() == second[1].lower() and first[2] == second[2]


class GitHub:
    """The REST and GraphQL APIs of GitHub with a token."""

    def __init__(self, token):
        self.token = token

    def _send(self, method, url, body=None, version=API_VERSION):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": version,
            "User-Agent": USER_AGENT,
        })
        with urllib.request.urlopen(request) as response:
            text = response.read().decode()
            return (json.loads(text) if text else None), response.headers.get("Link", "")

    def rest(self, method, path, body=None, version=API_VERSION):
        return self._send(method, f"{API}{path}", body, version)[0]

    def rest_all(self, path):
        """All the pages of a list."""
        url = f"{API}{path}{'&' if '?' in path else '?'}per_page=100"
        values = []
        while url:
            page, link = self._send("GET", url)
            values.extend(page)
            following = re.search(r'<([^>]+)>;\s*rel="next"', link)
            url = following.group(1) if following else None
        return values

    def graphql(self, query, variables):
        result = self._send("POST", f"{API}/graphql", {"query": query, "variables": variables})[0]
        if result.get("errors"):
            raise RuntimeError(f"GraphQL: {result['errors']}")
        return result["data"]

    def project(self, org, number):
        """The ID and the fields of a project of an organization: {"id", "fields": {name: {"id", "dataType"}}}."""
        data = self.graphql("""
            query($org: String!, $number: Int!) {
              organization(login: $org) {
                projectV2(number: $number) {
                  id
                  fields(first: 100) { nodes { ... on ProjectV2FieldCommon { id name dataType } } }
                }
              }
            }""", {"org": org, "number": number})
        project = data["organization"]["projectV2"]
        if project is None:
            raise RuntimeError(f"No project {number} in the organization {org}.")
        fields = {node["name"]: node for node in project["fields"]["nodes"] if node}
        return {"id": project["id"], "fields": fields}

    def items(self, project_id, field):
        """The issues of a project: {issue node ID: {"item": item ID, "estimate": value or None}}."""
        values = {}
        cursor = None
        while True:
            data = self.graphql("""
                query($id: ID!, $cursor: String, $field: String!) {
                  node(id: $id) {
                    ... on ProjectV2 {
                      items(first: 100, after: $cursor) {
                        pageInfo { hasNextPage endCursor }
                        nodes {
                          id
                          content { ... on Issue { id } }
                          fieldValueByName(name: $field) { ... on ProjectV2ItemFieldNumberValue { number } }
                        }
                      }
                    }
                  }
                }""", {"id": project_id, "cursor": cursor, "field": field})
            page = data["node"]["items"]
            for node in page["nodes"]:
                content = node.get("content") or {}
                if content.get("id"):
                    value = node.get("fieldValueByName") or {}
                    values[content["id"]] = {"item": node["id"], "estimate": value.get("number")}
            if not page["pageInfo"]["hasNextPage"]:
                return values
            cursor = page["pageInfo"]["endCursor"]

    def repositories(self, org):
        return [repo["name"] for repo in self.rest_all(f"/orgs/{org}/repos?type=all") if not repo.get("archived")]

    def open_issues(self, owner, repo):
        return [issue for issue in self.rest_all(f"/repos/{owner}/{repo}/issues?state=open")
                if "pull_request" not in issue]

    def issue(self, owner, repo, number):
        return self.rest("GET", f"/repos/{owner}/{repo}/issues/{number}")

    def add_item(self, project_id, content_id):
        data = self.graphql("""
            mutation($project: ID!, $content: ID!) {
              addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
            }""", {"project": project_id, "content": content_id})
        return data["addProjectV2ItemById"]["item"]["id"]

    def set_number(self, project_id, item_id, field_id, value):
        self.graphql("""
            mutation($project: ID!, $item: ID!, $field: ID!, $value: Float!) {
              updateProjectV2ItemFieldValue(
                input: {projectId: $project, itemId: $item, fieldId: $field, value: {number: $value}}
              ) { projectV2Item { id } }
            }""", {"project": project_id, "item": item_id, "field": field_id, "value": value})

    def issue_fields(self, org):
        """The date issue fields of an organization: {name: field ID}."""
        fields = self.rest("GET", f"/orgs/{org}/issue-fields", version=FIELDS_API_VERSION)
        return {field["name"]: field["id"] for field in fields if field.get("data_type") == "date"}

    def field_values(self, owner, repo, number):
        """The issue field values of an issue: {field ID: value}."""
        values = self.rest("GET", f"/repos/{owner}/{repo}/issues/{number}/issue-field-values",
                           version=FIELDS_API_VERSION)
        return {value["issue_field_id"]: value.get("value") for value in values}

    def set_field_values(self, owner, repo, number, values):
        """Sets issue field values of an issue from {field ID: value}. The other values of the issue stay."""
        body = {"issue_field_values": [{"field_id": field, "value": value} for field, value in values.items()]}
        self.rest("POST", f"/repos/{owner}/{repo}/issues/{number}/issue-field-values", body, FIELDS_API_VERSION)

    def blocked_by(self, owner, repo, number):
        """The IDs of the issues that block an issue."""
        return {issue["id"] for issue in
                self.rest_all(f"/repos/{owner}/{repo}/issues/{number}/dependencies/blocked_by")}

    def add_blocked_by(self, owner, repo, number, blocking_id):
        self.rest("POST", f"/repos/{owner}/{repo}/issues/{number}/dependencies/blocked_by", {"issue_id": blocking_id})


class Sync:
    """One synchronization. `errors` counts the operations that failed: the other operations continue."""

    def __init__(self, api, org, number, field="Estimate", dry_run=False, out=print, start_field="Start date",
                 target_field="Target date", start_field_id=None, target_field_id=None):
        self.api = api
        self.org = org
        self.number = number
        self.field = field
        self.date_fields = {"start": start_field, "target": target_field}
        self.date_field_ids = {"start": start_field_id, "target": target_field_id}
        self.dry_run = dry_run
        self.out = out
        self.errors = 0
        self._issues = {}
        self._blockers = {}

    def _error(self, message):
        self.errors += 1
        self.out(f"::warning::{message}")

    def _issue(self, ref):
        key = (ref[0].lower(), ref[1].lower(), ref[2])
        if key not in self._issues:
            self._issues[key] = self.api.issue(*ref)
        return self._issues[key]

    def _link(self, blocked, blocking):
        """Makes the issue `blocked` blocked by the issue `blocking`. Both are references."""
        if same_issue(blocked, blocking):
            return
        name = f"{blocked[0]}/{blocked[1]}#{blocked[2]} blocked by {blocking[0]}/{blocking[1]}#{blocking[2]}"
        try:
            blocking_id = self._issue(blocking)["id"]
            key = (blocked[0].lower(), blocked[1].lower(), blocked[2])
            if key not in self._blockers:
                self._blockers[key] = self.api.blocked_by(*blocked)
            if blocking_id in self._blockers[key]:
                return
            if not self.dry_run:
                self.api.add_blocked_by(*blocked, blocking_id)
            self._blockers[key].add(blocking_id)
            self.out(f"Dependency: {name}.")
        except (urllib.error.HTTPError, KeyError, TypeError) as error:
            self._error(f"No dependency {name}: {error}")

    def run(self):
        project = self.api.project(self.org, self.number)
        field = project["fields"].get(self.field)
        if field is None or field.get("dataType") != "NUMBER":
            self._error(f"The project has no number field \"{self.field}\": the estimates do not change.")
            field = None
        date_fields = self._date_fields()
        items = self.api.items(project["id"], self.field)
        for repo in self.api.repositories(self.org):
            for issue in self.api.open_issues(self.org, repo):
                self._sync_issue(project["id"], field, date_fields, items, repo, issue)
        return self.errors

    def _date_fields(self):
        """The IDs of the date issue fields: {"start": ID, "target": ID}, with no key for a field that is not found."""
        found = {key: field for key, field in self.date_field_ids.items() if field is not None}
        if len(found) == len(self.date_fields):
            return found
        try:
            by_name = self.api.issue_fields(self.org)
        except (urllib.error.HTTPError, RuntimeError) as error:
            by_name = {}
            self._error(f"The script cannot read the issue fields of {self.org}: {error}")
        for key, name in self.date_fields.items():
            if key in found:
                continue
            if name in by_name:
                found[key] = by_name[name]
            else:
                self._error(f"The organization has no date issue field \"{name}\": these dates do not change.")
        return found

    def _sync_dates(self, date_fields, ref, name, body):
        wanted = {date_fields[key]: (key, value) for key, value in dates(body).items()
                  if value is not None and key in date_fields}
        if not wanted:
            return
        try:
            current = self.api.field_values(*ref)
            changed = {field: value for field, (_, value) in wanted.items() if current.get(field) != value}
            if not changed:
                return
            if not self.dry_run:
                self.api.set_field_values(*ref, changed)
            for field, (key, value) in wanted.items():
                if field in changed:
                    self.out(f"{key.capitalize()}: {name} {value}.")
        except (urllib.error.HTTPError, RuntimeError) as error:
            self._error(f"No dates for {name}: {error}")

    def _sync_issue(self, project_id, field, date_fields, items, repo, issue):
        ref = (self.org, repo, issue["number"])
        name = f"{repo}#{issue['number']}"
        item = items.get(issue["node_id"])
        if item is None:
            try:
                item_id = "dry-run" if self.dry_run else self.api.add_item(project_id, issue["node_id"])
                item = {"item": item_id, "estimate": None}
                items[issue["node_id"]] = item
                self.out(f"Added: {name} {issue['title']}")
            except (urllib.error.HTTPError, RuntimeError) as error:
                self._error(f"{name} is not in the project: {error}")
                item = None
        hours = estimate(issue.get("body"))
        if field is not None and item is not None and hours is not None and item["estimate"] != hours:
            try:
                if not self.dry_run:
                    self.api.set_number(project_id, item["item"], field["id"], hours)
                item["estimate"] = hours
                self.out(f"Estimate: {name} {hours:g} h.")
            except (urllib.error.HTTPError, RuntimeError) as error:
                self._error(f"No estimate for {name}: {error}")
        self._sync_dates(date_fields, ref, name, issue.get("body"))
        blocked_by, blocks = dependencies(issue.get("body"), self.org, repo)
        for other in blocked_by:
            self._link(ref, other)
        for other in blocks:
            self._link(other, ref)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", required=True, help="the organization of the project and of the repositories")
    parser.add_argument("--project", type=int, required=True, help="the number of the project")
    parser.add_argument("--field", default="Estimate", help="the number field of the estimate, in hours")
    parser.add_argument("--start-field", default="Start date", help="the date issue field of the start of the work")
    parser.add_argument("--target-field", default="Target date", help="the date issue field of the end of the work")
    parser.add_argument("--start-field-id", type=int, help="the ID of the start field, when the token cannot read it")
    parser.add_argument("--target-field-id", type=int, help="the ID of the target field, when the token cannot read it")
    parser.add_argument("--dry-run", action="store_true", help="show the changes, and change nothing")
    args = parser.parse_args(argv)
    token = os.environ.get("PROJECT_TOKEN")
    if not token:
        print("No PROJECT_TOKEN. Give a token with the permissions of the documentation of this script.",
              file=sys.stderr)
        return 1
    sync = Sync(GitHub(token), args.org, args.project, args.field, args.dry_run, start_field=args.start_field,
                target_field=args.target_field, start_field_id=args.start_field_id,
                target_field_id=args.target_field_id)
    try:
        errors = sync.run()
    except urllib.error.HTTPError as error:
        print(f"GitHub refused {error.url}: {error.code} {error.read().decode()}", file=sys.stderr)
        return 1
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Done. Errors: {errors}.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
