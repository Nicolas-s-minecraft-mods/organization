# organization

Shared tools of the organization [Nicolas-s-minecraft-mods](https://github.com/Nicolas-s-minecraft-mods): project synchronization and issue management.

## Project synchronization

The open issues of all the repositories of the organization are in the project [Mods project](https://github.com/orgs/Nicolas-s-minecraft-mods/projects/2). The workflow **Project** runs each hour and with "Run workflow". It adds the open issues to the project and reads these lines in the description of each issue:

- `Estimate: 6h`: the work that is necessary, in hours. The workflow sets the field **Estimate** of the project.
- `Start: 2026-11-23`: the day the work starts. The workflow sets the field **Start date**.
- `Target: 2026-11-25`: the day the work ends. The workflow sets the field **Target date**.
- `Blocked by: #12, Folkhold#597`: the issues that must close before this issue can start.
- `Blocks: Folkhold#597`: the issues that wait for this issue.

An issue is `#12` (same repository), `Folkhold#597` (same organization), `owner/repo#12` or the URL of the issue. The lines can be in a list or in bold.

The workflow only adds or changes values. To remove an item, an estimate, a date or a dependency, use the issue or the project.

The script is `scripts/project_sync.py`. The secret `PROJECT_TOKEN` of the organization gives the permissions of the script: the organization permission "Projects" (read and write), and the repository permissions "Issues" (read and write) and "Metadata" (read) on all the repositories.

## Tests

```
python3 -m unittest discover -s scripts -p 'test_*.py'
```

## Contributions

- Write all code, comments, documentation, commit messages, issues and pull requests in English, in the style of ASD-STE100.
- One issue has one branch `<type>/<issue>-<short-name>` and one pull request to `main`. Use "Squash and merge".
