"""GitHub REST API interactions.

Stateless: a client is built per call from the bound token and closed
with it. The API base is configurable on the secret so the same agent
serves GitHub Enterprise (``https://ghe.example.com/api/v3``).
"""

import httpx
from decentai_sdk.base import ToolBase

#: Under the shortest function timeout, so a slow GitHub answers as a
#: readable error rather than a killed worker.
TIMEOUT = httpx.Timeout(20.0)

DEFAULT_API = "https://api.github.com"
API_VERSION = "2022-11-28"

#: A diff longer than this is cut, and says so.
DIFF_LIMIT = 20000


class GitHubError(Exception):
    """A refusal this tool can explain in one sentence."""


def error_message(response):
    """GitHub's own words for a refusal, when it gives any."""
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = ""
    if isinstance(body, dict):
        detail = str(body.get("message") or "")
        reasons = []
        for error in body.get("errors") or []:
            if isinstance(error, dict):
                reasons.append(str(error.get("message") or error.get("code")
                                   or error.get("field") or ""))
            else:
                reasons.append(str(error))
        reasons = [reason for reason in reasons if reason]
        if reasons:
            detail += " (" + "; ".join(reasons) + ")"
    detail = detail or response.text.strip()[:300] or response.reason_phrase
    return f"GitHub answered {response.status_code}: {detail}"


class GitHubTool(ToolBase):
    id = "github"

    # ------------------------------------------------------------------
    # The connection, and the one way every function runs
    # ------------------------------------------------------------------

    async def _connection(self, call):
        """An authenticated client, or GitHubError."""
        try:
            secret = await call.resources.use_secret("github_token")
        except Exception:
            raise GitHubError("No GitHub token is bound to this agent.")

        token = str(secret.get("token") or "").strip()
        if not token:
            raise GitHubError("The GitHub connection has no token.")
        api = (str(secret.get("api_base_url") or "").strip().rstrip("/")
               or DEFAULT_API)
        if "://" not in api:
            api = "https://" + api

        return httpx.AsyncClient(
            base_url=api,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=TIMEOUT,
        )

    async def _run(self, call, action):
        """Run one action against the connection. Every way it can fail
        comes back as an error result; nothing raises past here."""
        try:
            client = await self._connection(call)
            async with client:
                return await action(client), "success"
        except GitHubError as exc:
            return {"error": str(exc)}, "error"
        except httpx.HTTPStatusError as exc:
            return {"error": error_message(exc.response)}, "error"
        except httpx.HTTPError as exc:
            return {"error": f"Could not reach GitHub: {exc}"}, "error"

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------

    async def list_prs(self, call):
        owner = str(call.inputs["owner"]).strip()
        repo = str(call.inputs["repo"]).strip()
        state = str(call.inputs.get("state") or "open")
        limit = int(call.inputs.get("limit") or 30)
        await call.progress(f"Listing {state} pull requests in {owner}/{repo}")

        async def action(client):
            response = await client.get(f"/repos/{owner}/{repo}/pulls", params={
                "state": state, "per_page": limit,
                "sort": "updated", "direction": "desc",
            })
            response.raise_for_status()
            prs = []
            for pr in response.json() or []:
                prs.append({
                    "number": int(pr.get("number") or 0),
                    "title": str(pr.get("title") or ""),
                    "state": str(pr.get("state") or ""),
                    "author": str((pr.get("user") or {}).get("login") or ""),
                    "draft": bool(pr.get("draft", False)),
                    "url": str(pr.get("html_url") or ""),
                })
            return {"prs": prs}
        return await self._run(call, action)

    async def get_pr_diff(self, call):
        owner = str(call.inputs["owner"]).strip()
        repo = str(call.inputs["repo"]).strip()
        pr_number = int(call.inputs["pr_number"])
        await call.progress(f"Fetching the diff of {owner}/{repo}#{pr_number}")

        async def action(client):
            # The same resource, asked for as a diff rather than as JSON.
            response = await client.get(
                f"/repos/{owner}/{repo}/pulls/{pr_number}",
                headers={"Accept": "application/vnd.github.diff"})
            response.raise_for_status()
            diff = response.text
            truncated = len(diff) > DIFF_LIMIT
            if truncated:
                diff = diff[:DIFF_LIMIT] + "\n\n... (diff truncated)"
            return {"diff_content": diff, "truncated": truncated}
        return await self._run(call, action)

    async def create_issue(self, call):
        owner = str(call.inputs["owner"]).strip()
        repo = str(call.inputs["repo"]).strip()
        title = str(call.inputs["title"]).strip()
        body = str(call.inputs.get("body") or "")
        labels = [str(label) for label in call.inputs.get("labels") or []]
        await call.progress(f"Creating an issue in {owner}/{repo}")

        async def action(client):
            payload = {"title": title}
            if body:
                payload["body"] = body
            if labels:
                payload["labels"] = labels
            response = await client.post(
                f"/repos/{owner}/{repo}/issues", json=payload)
            response.raise_for_status()
            data = response.json()
            return {
                "number": int(data.get("number") or 0),
                "issue_url": str(data.get("html_url") or ""),
            }
        return await self._run(call, action)
