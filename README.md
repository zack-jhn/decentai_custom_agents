# Custom DecentAI Agents

Task-specific agents for [DecentAI](https://github.com/a-dirir/decentai).
Add this repository as an agent source, review a manifest, approve it.

| Agent | What it does | Credential |
|---|---|---|
| [`jira/`](jira/) | Jira Cloud: search with JQL, read issues, create, log work, transition, assign, comment | Site URL, account email, API token |
| [`github/`](github/) | GitHub: list pull requests, read diffs, create issues | Personal access token; API base URL only for GitHub Enterprise |

Reads are permission level 0; writes are level 1, so a chat at trust
level 1 or above runs them without pausing for approval.

## Jira notes

The agent speaks Jira Cloud REST API v3. Rich text — comments, worklog
comments, descriptions — is sent as Atlassian Document Format and read
back as plain text. Search uses `/search/jql`, which does not report a
total; the approximate-count endpoint supplies it, and the number of
issues returned stands in when that is unavailable.

People are assigned by account id. `search_users` finds it from a name
or email; `assign_issue` with `none` unassigns.

`execute_jql` returns one flat row per issue — key, then each requested
field as a readable value, then a link — so the platform's tables show
"In Progress" and "Ada" rather than the objects Jira answers with.

## Run the tests

The agents run the way production runs them — in their own worker
process, over the worker protocol — against a stub Jira or GitHub on
loopback that records what the agent sent. They need the DecentAI
platform on the path:

```bash
PYTHONPATH=/path/to/decentai python -m pytest tests -q
```

The first run builds the agents' environment under `tests/.workerenv`
(cached; delete it after changing a dependency).
