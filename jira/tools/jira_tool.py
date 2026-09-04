"""Jira Cloud (REST API v3) interactions.

Everything here follows from two facts about v3. Rich text — comments,
worklog comments, descriptions — is Atlassian Document Format (ADF), not
a string: text is wrapped on the way out and flattened on the way back.
And search is ``/search/jql``, which pages by token and never reports a
total; the approximate-count endpoint answers that separately.

Stateless: a client is built per call from the bound connection and
closed with it.
"""

import json

import httpx
from decentai_sdk.base import ToolBase

#: Under the shortest function timeout, so a slow Jira answers as a
#: readable error rather than a killed worker.
TIMEOUT = httpx.Timeout(20.0)

DEFAULT_JQL = (
    "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"
)

#: What a listing shows for one issue.
BRIEF_FIELDS = "summary,status,assignee,issuetype"

#: What the detail view shows.
DETAIL_FIELDS = (
    "summary,description,status,assignee,reporter,issuetype,priority,"
    "labels,created,updated"
)

#: Words that mean "nobody" when assigning.
UNASSIGN = {"", "none", "null", "nobody", "unassigned", "unassign", "-1"}


class JiraError(Exception):
    """A refusal this tool can explain in one sentence."""


def adf(text):
    """Plain text as an ADF document: blank-line-separated paragraphs,
    single newlines as hard breaks. Empty text nodes are invalid ADF, so
    empty lines are skipped rather than emitted."""
    content = []
    for paragraph in str(text or "").replace("\r\n", "\n").split("\n\n"):
        nodes = []
        for line in paragraph.split("\n"):
            if nodes:
                nodes.append({"type": "hardBreak"})
            if line:
                nodes.append({"type": "text", "text": line})
        content.append({"type": "paragraph", "content": nodes})
    return {"type": "doc", "version": 1, "content": content}


#: ADF nodes that end a line when flattened.
BLOCK_NODES = {
    "paragraph", "heading", "codeBlock", "blockquote", "listItem",
    "panel", "tableRow", "mediaSingle",
}


def plain_text(node):
    """Whatever Jira hands back for a rich-text field, as plain text — an
    ADF document (Cloud), a string (Data Center, or API v2), or nothing.
    Structure is kept only as far as line breaks and list dashes."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(plain_text(child) for child in node)
    if not isinstance(node, dict):
        return str(node)

    kind = node.get("type")
    attrs = node.get("attrs") or {}
    if kind == "text":
        return str(node.get("text") or "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji"):
        return str(attrs.get("text") or "")
    if kind == "inlineCard":
        return str(attrs.get("url") or "")
    if kind == "rule":
        return "---\n"

    inner = plain_text(node.get("content") or [])
    if kind == "listItem":
        inner = "- " + inner
    if kind == "doc":
        return inner.strip()
    return inner + ("\n" if kind in BLOCK_NODES else "")


def display(value):
    """A Jira field value as something a table cell can show.

    Jira answers with objects for almost everything — a status is
    ``{"name": ...}``, a person ``{"displayName": ...}``, a select
    option ``{"value": ...}``, a description an ADF document, labels a
    list. A cell wants one string; this is the one place that decides
    which part of each shape is the readable one."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(str(display(item)) for item in value
                         if display(item) != "")
    if isinstance(value, dict):
        if value.get("type") == "doc":
            return plain_text(value)
        for name in ("displayName", "name", "value", "key", "emailAddress"):
            if value.get(name):
                return str(value[name])
        return json.dumps(value, default=str)
    return str(value)


def error_message(response):
    """Jira's own words for a refusal, when it gives any."""
    try:
        body = response.json()
    except ValueError:
        body = None
    messages = []
    if isinstance(body, dict):
        messages += [str(m) for m in body.get("errorMessages") or []]
        messages += [f"{field}: {reason}"
                     for field, reason in (body.get("errors") or {}).items()]
    detail = ("; ".join(messages) or response.text.strip()[:300]
              or response.reason_phrase)
    return f"Jira answered {response.status_code}: {detail}"


def person(value, absent):
    return str((value or {}).get("displayName") or absent)


class JiraTool(ToolBase):
    id = "jira"

    # ------------------------------------------------------------------
    # The connection, and the one way every function runs
    # ------------------------------------------------------------------

    async def _connection(self, call):
        """The bound connection as (site url, client), or JiraError."""
        try:
            secret = await call.resources.use_secret("jira_connection")
        except Exception:
            raise JiraError("No Jira connection is bound to this agent.")

        base_url = str(secret.get("base_url") or "").strip().rstrip("/")
        email = str(secret.get("email") or "").strip()
        token = str(secret.get("api_token") or "").strip()
        missing = [name for name, value in (
            ("base_url", base_url), ("email", email), ("api_token", token),
        ) if not value]
        if missing:
            raise JiraError(
                "The Jira connection is incomplete — missing: "
                + ", ".join(missing) + ".")
        if "://" not in base_url:
            base_url = "https://" + base_url

        client = httpx.AsyncClient(
            base_url=base_url + "/rest/api/3",
            auth=httpx.BasicAuth(email, token),
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        return base_url, client

    async def _run(self, call, action):
        """Run one action against the connection. Every way it can fail
        comes back as an error result; nothing raises past here."""
        try:
            base_url, client = await self._connection(call)
            async with client:
                return await action(base_url, client), "success"
        except JiraError as exc:
            return {"error": str(exc)}, "error"
        except httpx.HTTPStatusError as exc:
            return {"error": error_message(exc.response)}, "error"
        except httpx.HTTPError as exc:
            return {"error": f"Could not reach Jira: {exc}"}, "error"

    # ------------------------------------------------------------------
    # Shared requests
    # ------------------------------------------------------------------

    async def _search(self, client, jql, limit, fields):
        response = await client.get("/search/jql", params={
            "jql": jql, "maxResults": limit, "fields": fields,
        })
        response.raise_for_status()
        return response.json().get("issues") or []

    async def _count(self, client, jql, fallback):
        """How many issues match — Jira's approximate count, or what was
        returned when the count cannot be had. A count that fails must
        not fail the search it decorates."""
        try:
            response = await client.post(
                "/search/approximate-count", json={"jql": jql})
            response.raise_for_status()
            return int(response.json().get("count") or 0)
        except (httpx.HTTPError, ValueError, TypeError):
            return fallback

    @staticmethod
    def _brief(base_url, issue):
        fields = issue.get("fields") or {}
        return {
            "key": str(issue.get("key") or ""),
            "summary": str(fields.get("summary") or ""),
            "status": str((fields.get("status") or {}).get("name") or ""),
            "assignee": person(fields.get("assignee"), "Unassigned"),
            "issue_type": str((fields.get("issuetype") or {}).get("name") or ""),
            "url": f"{base_url}/browse/{issue.get('key')}",
        }

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_issues(self, call):
        jql = str(call.inputs.get("jql") or DEFAULT_JQL)
        limit = int(call.inputs.get("limit") or 10)
        await call.progress(f"Searching Jira issues (limit {limit})")

        async def action(base_url, client):
            issues = await self._search(client, jql, limit, BRIEF_FIELDS)
            return {
                "issues": [self._brief(base_url, issue) for issue in issues],
                "total": await self._count(client, jql, len(issues)),
            }
        return await self._run(call, action)

    async def execute_jql(self, call):
        jql = str(call.inputs["jql"])
        fields = ([str(f) for f in call.inputs.get("fields") or []]
                  or ["summary", "status", "assignee"])
        max_results = int(call.inputs.get("max_results") or 50)
        await call.progress(f"Executing JQL: {jql}")

        async def action(base_url, client):
            issues = await self._search(
                client, jql, max_results, ",".join(fields))
            rows = []
            for issue in issues:
                # One flat row per issue, one readable cell per asked
                # field, in the order asked — what a table needs, and
                # what the platform renders straight from storage.
                found = issue.get("fields") or {}
                row = {"key": str(issue.get("key") or "")}
                for name in fields:
                    row[name] = display(found.get(name))
                row["url"] = f"{base_url}/browse/{issue.get('key')}"
                rows.append(row)
            return {"issues": rows,
                    "total": await self._count(client, jql, len(rows))}
        return await self._run(call, action)

    async def get_issue(self, call):
        issue_key = str(call.inputs["issue_key"]).strip()
        await call.progress(f"Fetching {issue_key}")

        async def action(base_url, client):
            response = await client.get(
                f"/issue/{issue_key}", params={"fields": DETAIL_FIELDS})
            response.raise_for_status()
            data = response.json()
            fields = data.get("fields") or {}
            key = str(data.get("key") or issue_key)
            return {
                "key": key,
                "summary": str(fields.get("summary") or ""),
                "description": plain_text(fields.get("description")),
                "status": str((fields.get("status") or {}).get("name") or ""),
                "assignee": person(fields.get("assignee"), "Unassigned"),
                "reporter": person(fields.get("reporter"), "Unknown"),
                "issue_type": str((fields.get("issuetype") or {}).get("name") or ""),
                "priority": str((fields.get("priority") or {}).get("name") or ""),
                "labels": [str(label) for label in fields.get("labels") or []],
                "created": str(fields.get("created") or ""),
                "updated": str(fields.get("updated") or ""),
                "url": f"{base_url}/browse/{key}",
            }
        return await self._run(call, action)

    async def search_users(self, call):
        query = str(call.inputs["query"]).strip()
        limit = int(call.inputs.get("limit") or 10)
        await call.progress(f"Searching Jira users for '{query}'")

        async def action(base_url, client):
            response = await client.get(
                "/user/search", params={"query": query, "maxResults": limit})
            response.raise_for_status()
            users = []
            for user in response.json() or []:
                users.append({
                    "account_id": str(user.get("accountId") or ""),
                    "display_name": str(user.get("displayName") or ""),
                    # Hidden unless the person's privacy settings allow it.
                    "email": str(user.get("emailAddress") or ""),
                    "active": bool(user.get("active", True)),
                })
            return {"users": users}
        return await self._run(call, action)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def log_work(self, call):
        issue_key = str(call.inputs["issue_key"]).strip()
        time_spent = str(call.inputs["time_spent"]).strip()
        comment = str(call.inputs.get("comment") or "").strip()
        await call.progress(f"Logging {time_spent} on {issue_key}")

        async def action(base_url, client):
            payload = {"timeSpent": time_spent}
            if comment:
                payload["comment"] = adf(comment)
            response = await client.post(
                f"/issue/{issue_key}/worklog", json=payload)
            response.raise_for_status()
            return {
                "issue_key": issue_key,
                "worklog_id": str(response.json().get("id") or ""),
                "message": f"Logged {time_spent} on {issue_key}.",
            }
        return await self._run(call, action)

    async def transition_issue(self, call):
        issue_key = str(call.inputs["issue_key"]).strip()
        wanted = str(call.inputs["new_status"]).strip()
        await call.progress(f"Moving {issue_key} to {wanted}")

        async def action(base_url, client):
            path = f"/issue/{issue_key}/transitions"
            response = await client.get(path)
            response.raise_for_status()
            transitions = response.json().get("transitions") or []

            # A person names the STATUS they want ("Done"); Jira offers
            # TRANSITIONS ("Mark as done") that lead to one. Either name
            # is accepted, case-insensitively.
            target = wanted.lower()
            chosen = next((t for t in transitions if target in (
                str(t.get("name") or "").lower(),
                str((t.get("to") or {}).get("name") or "").lower(),
            )), None)
            if chosen is None:
                offered = sorted({
                    str((t.get("to") or {}).get("name") or t.get("name") or "")
                    for t in transitions})
                raise JiraError(
                    f"{issue_key} cannot move to '{wanted}' from here. "
                    f"Available: {', '.join(offered) or 'none'}.")

            response = await client.post(
                path, json={"transition": {"id": str(chosen["id"])}})
            response.raise_for_status()
            reached = str((chosen.get("to") or {}).get("name")
                          or chosen.get("name") or wanted)
            return {"issue_key": issue_key, "status": reached,
                    "message": f"Moved {issue_key} to {reached}."}
        return await self._run(call, action)

    async def create_issue(self, call):
        project_key = str(call.inputs["project_key"]).strip()
        summary = str(call.inputs["summary"]).strip()
        issue_type = str(call.inputs["issue_type"]).strip()
        description = str(call.inputs.get("description") or "").strip()
        await call.progress(f"Creating a {issue_type} in {project_key}")

        async def action(base_url, client):
            fields = {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type},
            }
            if description:
                fields["description"] = adf(description)
            response = await client.post("/issue", json={"fields": fields})
            response.raise_for_status()
            key = str(response.json().get("key") or "")
            return {"issue_key": key, "url": f"{base_url}/browse/{key}",
                    "message": f"Created {key}."}
        return await self._run(call, action)

    async def assign_issue(self, call):
        issue_key = str(call.inputs["issue_key"]).strip()
        account_id = str(call.inputs.get("account_id") or "").strip()
        unassign = account_id.lower() in UNASSIGN
        await call.progress(
            f"Unassigning {issue_key}" if unassign else f"Assigning {issue_key}")

        async def action(base_url, client):
            response = await client.put(
                f"/issue/{issue_key}/assignee",
                json={"accountId": None if unassign else account_id})
            response.raise_for_status()
            return {"issue_key": issue_key, "message": (
                f"Unassigned {issue_key}." if unassign
                else f"Assigned {issue_key} to account {account_id}.")}
        return await self._run(call, action)

    async def add_comment(self, call):
        issue_key = str(call.inputs["issue_key"]).strip()
        comment = str(call.inputs["comment"]).strip()
        if not comment:
            return {"error": "The comment is empty."}, "error"
        await call.progress(f"Commenting on {issue_key}")

        async def action(base_url, client):
            response = await client.post(
                f"/issue/{issue_key}/comment", json={"body": adf(comment)})
            response.raise_for_status()
            return {
                "issue_key": issue_key,
                "comment_id": str(response.json().get("id") or ""),
                "message": f"Comment added to {issue_key}.",
            }
        return await self._run(call, action)
