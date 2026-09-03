"""Jira API interactions."""

import httpx
from decentai_sdk.base import ToolBase

class JiraTool(ToolBase):
    id = "jira"

    async def _get_client(self, call):
        """Helper to get an authenticated httpx client and base URL."""
        try:
            secret = await call.resources.use_secret("jira_connection")
        except Exception:
            raise ValueError("No Jira connection secret bound")
        
        base_url = secret.get("base_url", "").rstrip("/")
        email = secret.get("email")
        api_token = secret.get("api_token")
        
        if not all([base_url, email, api_token]):
            raise ValueError("Incomplete Jira connection secrets")
            
        auth = httpx.BasicAuth(email, api_token)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        return base_url, httpx.AsyncClient(auth=auth, headers=headers)

    async def list_issues(self, call):
        jql = call.inputs.get("jql") or "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"
        limit = int(call.inputs.get("limit") or 10)

        await call.progress(f"Searching Jira issues (limit: {limit})")
        
        try:
            base_url, client = await self._get_client(call)
            async with client:
                url = f"{base_url}/rest/api/3/search/jql"
                params = {"jql": jql, "maxResults": limit, "fields": "summary,status"}
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                issues = []
                for issue in data.get("issues", []):
                    issues.append({
                        "key": issue["key"],
                        "summary": issue["fields"]["summary"],
                        "status": issue["fields"]["status"]["name"]
                    })
                
                return {"issues": issues, "total": data.get("total", 0)}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"error": f"Jira API HTTP error: {e.response.status_code} - {e.response.text}"}, "error"
        except Exception as e:
            return {"error": str(e)}, "error"

    async def log_work(self, call):
        issue_key = str(call.inputs["issue_key"])
        time_spent = str(call.inputs["time_spent"])
        comment = call.inputs.get("comment", "")

        await call.progress(f"Logging {time_spent} on {issue_key}")
        
        try:
            base_url, client = await self._get_client(call)
            async with client:
                url = f"{base_url}/rest/api/3/issue/{issue_key}/worklog"
                payload = {"timeSpent": time_spent}
                if comment:
                    payload["comment"] = comment
                    
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                
                return {"success": True, "message": f"Successfully logged {time_spent} to {issue_key}"}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"API error: {e.response.text}"}, "error"
        except Exception as e:
            return {"success": False, "message": str(e)}, "error"

    async def transition_issue(self, call):
        issue_key = str(call.inputs["issue_key"])
        new_status = str(call.inputs["new_status"]).lower()

        await call.progress(f"Moving {issue_key} to {new_status}")
        
        try:
            base_url, client = await self._get_client(call)
            async with client:
                # First, get available transitions
                url = f"{base_url}/rest/api/3/issue/{issue_key}/transitions"
                resp = await client.get(url)
                resp.raise_for_status()
                
                transitions = resp.json().get("transitions", [])
                target_transition = None
                
                for t in transitions:
                    if t["name"].lower() == new_status:
                        target_transition = t
                        break
                        
                if not target_transition:
                    available = [t["name"] for t in transitions]
                    return {
                        "success": False, 
                        "message": f"Status '{new_status}' not found. Available: {', '.join(available)}"
                    }, "error"
                
                # Perform the transition
                payload = {"transition": {"id": target_transition["id"]}}
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                
                return {"success": True, "message": f"Successfully transitioned {issue_key} to {target_transition['name']}"}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"API error: {e.response.text}"}, "error"
        except Exception as e:
            return {"success": False, "message": str(e)}, "error"

    async def get_issue(self, call):
        issue_key = str(call.inputs["issue_key"])
        await call.progress(f"Fetching details for {issue_key}")
        
        try:
            base_url, client = await self._get_client(call)
            async with client:
                url = f"{base_url}/rest/api/3/issue/{issue_key}"
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                fields = data.get("fields", {})
                
                return {
                    "key": data.get("key"),
                    "summary": fields.get("summary", ""),
                    "description": fields.get("description", "") or "",
                    "status": fields.get("status", {}).get("name", ""),
                    "assignee": fields.get("assignee", {}).get("displayName", "Unassigned") if fields.get("assignee") else "Unassigned",
                    "reporter": fields.get("reporter", {}).get("displayName", "Unknown") if fields.get("reporter") else "Unknown"
                }, "success"
                
        except httpx.HTTPStatusError as e:
            return {"error": f"API error: {e.response.text}"}, "error"
        except Exception as e:
            return {"error": str(e)}, "error"

    async def create_issue(self, call):
        project_key = str(call.inputs["project_key"])
        summary = str(call.inputs["summary"])
        issue_type = str(call.inputs["issue_type"])
        description = call.inputs.get("description", "")

        await call.progress(f"Creating {issue_type} in {project_key}")
        
        try:
            base_url, client = await self._get_client(call)
            async with client:
                url = f"{base_url}/rest/api/3/issue"
                payload = {
                    "fields": {
                        "project": {"key": project_key},
                        "summary": summary,
                        "description": description,
                        "issuetype": {"name": issue_type}
                    }
                }
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                
                issue_key = resp.json().get("key")
                return {"success": True, "issue_key": issue_key, "message": f"Created {issue_key}"}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"success": False, "issue_key": "", "message": f"API error: {e.response.text}"}, "error"
        except Exception as e:
            return {"success": False, "issue_key": "", "message": str(e)}, "error"

    async def assign_issue(self, call):
        issue_key = str(call.inputs["issue_key"])
        account_id = str(call.inputs["account_id"])

        await call.progress(f"Assigning {issue_key}")
        
        try:
            base_url, client = await self._get_client(call)
            async with client:
                url = f"{base_url}/rest/api/3/issue/{issue_key}/assignee"
                # Jira Cloud uses accountId, Jira Data Center uses name. 
                # We'll try accountId first as it's the most common target right now for Cloud.
                payload = {"accountId": account_id}
                resp = await client.put(url, json=payload)
                resp.raise_for_status()
                
                return {"success": True, "message": f"Successfully assigned {issue_key}"}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"API error: {e.response.text}"}, "error"
        except Exception as e:
            return {"success": False, "message": str(e)}, "error"

    async def add_comment(self, call):
        issue_key = str(call.inputs["issue_key"])
        comment = str(call.inputs["comment"])

        await call.progress(f"Adding comment to {issue_key}")
        
        try:
            base_url, client = await self._get_client(call)
            async with client:
                url = f"{base_url}/rest/api/3/issue/{issue_key}/comment"
                payload = {"body": comment}
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                
                return {"success": True, "message": f"Comment added to {issue_key}"}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"success": False, "message": f"API error: {e.response.text}"}, "error"
        except Exception as e:
            return {"success": False, "message": str(e)}, "error"

    async def execute_jql(self, call):
        jql = str(call.inputs["jql"])
        fields = call.inputs.get("fields") or ["summary", "status", "assignee"]
        max_results = int(call.inputs.get("max_results") or 50)

        await call.progress(f"Executing custom JQL: {jql}")
        
        try:
            base_url, client = await self._get_client(call)
            async with client:
                url = f"{base_url}/rest/api/3/search/jql"
                params = {"jql": jql, "maxResults": max_results, "fields": ",".join(fields)}
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                issues = []
                for issue in data.get("issues", []):
                    issues.append({
                        "key": issue.get("key"),
                        "fields": issue.get("fields", {})
                    })
                
                return {"issues": issues, "total": data.get("total", 0)}, "success"
                
        except httpx.HTTPStatusError as e:
            return {"error": f"API error: {e.response.text}"}, "error"
        except Exception as e:
            return {"error": str(e)}, "error"