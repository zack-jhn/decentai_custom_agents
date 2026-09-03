"""GitHub API interactions."""

import httpx
from decentai_sdk.base import ToolBase

class GitHubTool(ToolBase):
    id = "github"

    async def _get_client(self, call, accept_header="application/vnd.github.v3+json"):
        """Helper to get an authenticated httpx client."""
        try:
            secret = await call.resources.use_secret("github_token")
        except Exception:
            raise ValueError("No GitHub token bound")
        
        token = secret.get("token")
        if not token:
            raise ValueError("Incomplete GitHub connection secrets")
            
        headers = {
            "Accept": accept_header,
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        return httpx.AsyncClient(headers=headers, base_url="https://api.github.com")

    async def list_prs(self, call):
        owner = str(call.inputs["owner"])
        repo = str(call.inputs["repo"])

        await call.progress(f"Fetching open PRs for {owner}/{repo}")
        
        try:
            client = await self._get_client(call)
            async with client:
                url = f"/repos/{owner}/{repo}/pulls"
                params = {"state": "open"}
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                prs = []
                for pr in data:
                    prs.append({
                        "number": pr["number"],
                        "title": pr["title"],
                        "state": pr["state"],
                        "author": pr["user"]["login"]
                    })
                
                return {"prs": prs}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"error": f"API error: {e.response.status_code} - {e.response.text}"}, "error"
        except Exception as e:
            return {"error": str(e)}, "error"

    async def get_pr_diff(self, call):
        owner = str(call.inputs["owner"])
        repo = str(call.inputs["repo"])
        pr_number = int(call.inputs["pr_number"])

        await call.progress(f"Fetching diff for PR #{pr_number}")
        
        try:
            # We use a special Accept header to tell GitHub to return the raw diff
            client = await self._get_client(call, accept_header="application/vnd.github.v3.diff")
            async with client:
                url = f"/repos/{owner}/{repo}/pulls/{pr_number}"
                resp = await client.get(url)
                resp.raise_for_status()
                
                diff = resp.text
                # Truncate if the diff is massive
                if len(diff) > 20000:
                    diff = diff[:20000] + "\n\n... (Diff truncated due to size)"
                
                return {"diff_content": diff}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"error": f"API error: {e.response.text}"}, "error"
        except Exception as e:
            return {"error": str(e)}, "error"

    async def create_issue(self, call):
        owner = str(call.inputs["owner"])
        repo = str(call.inputs["repo"])
        title = str(call.inputs["title"])
        body = str(call.inputs["body"])

        await call.progress(f"Creating issue in {owner}/{repo}")
        
        try:
            client = await self._get_client(call)
            async with client:
                url = f"/repos/{owner}/{repo}/issues"
                payload = {"title": title, "body": body}
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                
                data = resp.json()
                return {"success": True, "issue_url": data["html_url"]}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"success": False, "issue_url": f"API error: {e.response.text}"}, "error"
        except Exception as e:
            return {"success": False, "issue_url": str(e)}, "error"