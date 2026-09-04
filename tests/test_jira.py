"""The Jira agent against a stub Jira Cloud: what it sends, and what it
makes of what comes back."""

import pytest

from tests.conftest import Invoker

API = "/rest/api/3"


def paragraph(*runs):
    return {"type": "paragraph", "content": list(runs)}


def text(value):
    return {"type": "text", "text": value}


@pytest.fixture()
def jira(agents, stub):
    return Invoker(agents["jira"], {"jira__jira_connection": {
        "base_url": stub.url, "email": "dev@example.com",
        "api_token": "token-123",
    }})


def test_both_agents_implement_their_manifests(agents):
    """Verified the way installation verifies: a worker spawned from the
    agent's own environment imports the code and checks every declared
    function has a method."""
    from ai_runtime.agents.worker_handle import WorkerHandle

    assert set(agents) == {"jira", "github"}
    for agent in agents.values():
        errors = WorkerHandle.probe(
            agent.environment.python, agent.folder, agent.manifest.document)
        assert errors == [], errors


# ---- reads ---------------------------------------------------------

def test_list_issues_defaults_to_my_open_work(jira, stub):
    stub.on("GET", f"{API}/search/jql", {"issues": [{
        "key": "DEV-1", "fields": {
            "summary": "Fix login", "status": {"name": "In Progress"},
            "assignee": {"displayName": "Ada"}, "issuetype": {"name": "Bug"},
        }}], "isLast": True})
    stub.on("POST", f"{API}/search/approximate-count", {"count": 7})

    result, status = jira("jira.list_issues")

    assert status == "success", result
    assert result == {"total": 7, "issues": [{
        "key": "DEV-1", "summary": "Fix login", "status": "In Progress",
        "assignee": "Ada", "issue_type": "Bug",
        "url": f"{stub.url}/browse/DEV-1",
    }]}
    sent = stub.sent("GET", f"{API}/search/jql")[0]
    assert sent["headers"]["authorization"].startswith("Basic ")
    assert sent["query"]["jql"].startswith("assignee = currentUser()")
    assert sent["query"]["maxResults"] == "10"
    assert stub.sent("POST", f"{API}/search/approximate-count")[0]["json"] == {
        "jql": sent["query"]["jql"]}


def test_a_failing_count_does_not_fail_the_search(jira, stub):
    stub.on("GET", f"{API}/search/jql", {"issues": [
        {"key": "DEV-1", "fields": {"summary": "a", "status": {"name": "To Do"}}},
        {"key": "DEV-2", "fields": {"summary": "b", "status": {"name": "To Do"}}},
    ]})
    result, status = jira("jira.list_issues", {"jql": "project = DEV", "limit": 2})
    assert status == "success", result
    assert result["total"] == 2
    assert [i["assignee"] for i in result["issues"]] == ["Unassigned", "Unassigned"]


def test_execute_jql_returns_one_flat_readable_row_per_issue(jira, stub):
    """Jira answers with objects for almost every field; a table wants
    one cell per column. Each asked field becomes one readable value, in
    the order asked, so nothing renders as [object Object]."""
    stub.on("GET", f"{API}/search/jql", {"issues": [{"key": "DEV-3", "fields": {
        "summary": "Rotate keys",
        "priority": {"self": "https://x/priority/2", "id": "2", "name": "High"},
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "assignee": {"accountId": "5b10", "displayName": "Ada", "active": True},
        "issuetype": {"id": "10001", "name": "Task", "subtask": False},
        "labels": ["security", "ops"],
        "components": [{"id": "1", "name": "auth"}, {"id": "2", "name": "infra"}],
        "customfield_10020": {"id": "3", "value": "Q4"},
        "duedate": None,
        "description": {"type": "doc", "version": 1,
                        "content": [paragraph(text("Before Friday"))]},
        "timespent": 3600,
    }}]})
    fields = ["summary", "priority", "status", "assignee", "issuetype", "labels",
              "components", "customfield_10020", "duedate", "description", "timespent"]

    result, status = jira("jira.execute_jql", {
        "jql": "priority = High", "fields": fields, "max_results": 5})

    assert status == "success", result
    assert result["issues"] == [{
        "key": "DEV-3",
        "summary": "Rotate keys",
        "priority": "High",
        "status": "In Progress",
        "assignee": "Ada",
        "issuetype": "Task",
        "labels": "security, ops",
        "components": "auth, infra",
        "customfield_10020": "Q4",
        "duedate": "",
        "description": "Before Friday",
        "timespent": 3600,
        "url": f"{stub.url}/browse/DEV-3",
    }]
    # Column order is the order asked, key first and the link last.
    assert list(result["issues"][0]) == ["key", *fields, "url"]
    sent = stub.sent("GET", f"{API}/search/jql")[0]
    assert sent["query"] == {"jql": "priority = High", "maxResults": "5",
                             "fields": ",".join(fields)}


def test_execute_jql_defaults_to_summary_status_and_assignee(jira, stub):
    stub.on("GET", f"{API}/search/jql", {"issues": [{"key": "DEV-4", "fields": {
        "summary": "s", "status": {"name": "Done"}, "assignee": None}}]})
    result, status = jira("jira.execute_jql", {"jql": "project = DEV"})
    assert status == "success", result
    assert result["issues"] == [{"key": "DEV-4", "summary": "s", "status": "Done",
                                 "assignee": "", "url": f"{stub.url}/browse/DEV-4"}]


def test_get_issue_flattens_the_adf_description(jira, stub):
    stub.on("GET", f"{API}/issue/DEV-5", {"key": "DEV-5", "fields": {
        "summary": "Write docs",
        "description": {"type": "doc", "version": 1, "content": [
            paragraph(text("First line"), {"type": "hardBreak"}, text("same paragraph")),
            paragraph(text("Second paragraph with "),
                      {"type": "mention", "attrs": {"text": "@Ada"}}),
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [paragraph(text("one item"))]},
                {"type": "listItem", "content": [paragraph(text("another"))]},
            ]},
        ]},
        "status": {"name": "To Do"}, "assignee": None,
        "reporter": {"displayName": "Grace"}, "issuetype": {"name": "Task"},
        "priority": None, "labels": ["docs"],
        "created": "2026-09-01T10:00:00.000+0000", "updated": "2026-09-02T10:00:00.000+0000",
    }})

    result, status = jira("jira.get_issue", {"issue_key": "DEV-5"})

    assert status == "success", result
    assert result["description"] == (
        "First line\nsame paragraph\nSecond paragraph with @Ada\n"
        "- one item\n\n- another")
    assert result["assignee"] == "Unassigned"
    assert result["reporter"] == "Grace"
    assert result["priority"] == ""
    assert result["labels"] == ["docs"]
    assert result["url"] == f"{stub.url}/browse/DEV-5"
    assert stub.sent("GET", f"{API}/issue/DEV-5")[0]["query"]["fields"].startswith(
        "summary,description")


def test_get_issue_accepts_a_plain_string_description(jira, stub):
    """Data Center and API v2 hand back a string; that must read too."""
    stub.on("GET", f"{API}/issue/DEV-6", {"key": "DEV-6", "fields": {
        "summary": "s", "description": "plain text", "status": {"name": "Done"}}})
    result, status = jira("jira.get_issue", {"issue_key": "DEV-6"})
    assert status == "success", result
    assert result["description"] == "plain text"


def test_search_users_gives_the_account_id_assign_needs(jira, stub):
    stub.on("GET", f"{API}/user/search", [
        {"accountId": "5b10ac8d", "displayName": "Ada Lovelace",
         "emailAddress": "ada@example.com", "active": True},
        {"accountId": "5b10ac8e", "displayName": "Ada Byron", "active": False},
    ])
    result, status = jira("jira.search_users", {"query": "ada"})
    assert status == "success", result
    assert result["users"] == [
        {"account_id": "5b10ac8d", "display_name": "Ada Lovelace",
         "email": "ada@example.com", "active": True},
        {"account_id": "5b10ac8e", "display_name": "Ada Byron",
         "email": "", "active": False},
    ]
    assert stub.sent("GET", f"{API}/user/search")[0]["query"] == {
        "query": "ada", "maxResults": "10"}


# ---- writes: v3 wants ADF for every rich-text field -----------------

def test_add_comment_sends_adf(jira, stub):
    stub.on("POST", f"{API}/issue/DEV-1/comment", {"id": "10001"}, status=201)

    result, status = jira("jira.add_comment", {
        "issue_key": "DEV-1", "comment": "Looks good\nShip it"}, level=1)

    assert status == "success", result
    assert result == {"issue_key": "DEV-1", "comment_id": "10001",
                      "message": "Comment added to DEV-1."}
    assert stub.sent("POST", f"{API}/issue/DEV-1/comment")[0]["json"] == {
        "body": {"type": "doc", "version": 1, "content": [
            paragraph(text("Looks good"), {"type": "hardBreak"}, text("Ship it"))]}}


def test_log_work_sends_the_comment_as_adf_and_omits_it_when_empty(jira, stub):
    stub.on("POST", f"{API}/issue/DEV-1/worklog", {"id": "200"}, status=201)

    result, status = jira("jira.log_work", {
        "issue_key": "DEV-1", "time_spent": "2h", "comment": "Pairing"}, level=1)
    assert status == "success", result
    assert result == {"issue_key": "DEV-1", "worklog_id": "200",
                      "message": "Logged 2h on DEV-1."}

    jira("jira.log_work", {"issue_key": "DEV-1", "time_spent": "30m"}, level=1)

    first, second = stub.sent("POST", f"{API}/issue/DEV-1/worklog")
    assert first["json"] == {"timeSpent": "2h", "comment": {
        "type": "doc", "version": 1, "content": [paragraph(text("Pairing"))]}}
    assert second["json"] == {"timeSpent": "30m"}


def test_create_issue_wraps_the_description(jira, stub):
    stub.on("POST", f"{API}/issue", {"id": "10", "key": "DEV-9"}, status=201)

    result, status = jira("jira.create_issue", {
        "project_key": "DEV", "summary": "New thing", "issue_type": "Task",
        "description": "Why\n\nHow"}, level=1)

    assert status == "success", result
    assert result == {"issue_key": "DEV-9", "url": f"{stub.url}/browse/DEV-9",
                      "message": "Created DEV-9."}
    assert stub.sent("POST", f"{API}/issue")[0]["json"] == {"fields": {
        "project": {"key": "DEV"}, "summary": "New thing",
        "issuetype": {"name": "Task"},
        "description": {"type": "doc", "version": 1, "content": [
            paragraph(text("Why")), paragraph(text("How"))]}}}


def test_create_issue_without_a_description_sends_none(jira, stub):
    stub.on("POST", f"{API}/issue", {"key": "DEV-10"}, status=201)
    jira("jira.create_issue", {
        "project_key": "DEV", "summary": "Bare", "issue_type": "Bug"}, level=1)
    assert "description" not in stub.sent("POST", f"{API}/issue")[0]["json"]["fields"]


# ---- transitions ---------------------------------------------------

TRANSITIONS = {"transitions": [
    {"id": "11", "name": "Start work", "to": {"name": "In Progress"}},
    {"id": "31", "name": "Mark as done", "to": {"name": "Done"}},
]}


def test_transition_matches_the_status_a_person_names(jira, stub):
    stub.on("GET", f"{API}/issue/DEV-1/transitions", TRANSITIONS)
    stub.on("POST", f"{API}/issue/DEV-1/transitions", "", status=204)

    result, status = jira("jira.transition_issue", {
        "issue_key": "DEV-1", "new_status": "done"}, level=1)

    assert status == "success", result
    assert result == {"issue_key": "DEV-1", "status": "Done",
                      "message": "Moved DEV-1 to Done."}
    assert stub.sent("POST", f"{API}/issue/DEV-1/transitions")[0]["json"] == {
        "transition": {"id": "31"}}


def test_transition_also_matches_the_transition_name(jira, stub):
    stub.on("GET", f"{API}/issue/DEV-1/transitions", TRANSITIONS)
    stub.on("POST", f"{API}/issue/DEV-1/transitions", "", status=204)
    result, status = jira("jira.transition_issue", {
        "issue_key": "DEV-1", "new_status": "Start Work"}, level=1)
    assert status == "success", result
    assert result["status"] == "In Progress"


def test_an_unreachable_status_lists_what_is_reachable(jira, stub):
    stub.on("GET", f"{API}/issue/DEV-1/transitions", TRANSITIONS)
    result, status = jira("jira.transition_issue", {
        "issue_key": "DEV-1", "new_status": "Released"}, level=1)
    assert status == "error"
    assert result["error"] == (
        "DEV-1 cannot move to 'Released' from here. Available: Done, In Progress.")
    assert stub.sent("POST", f"{API}/issue/DEV-1/transitions") == []


# ---- assignment ----------------------------------------------------

def test_assign_by_account_id_and_unassign_by_none(jira, stub):
    stub.on("PUT", f"{API}/issue/DEV-1/assignee", "", status=204)

    result, status = jira("jira.assign_issue", {
        "issue_key": "DEV-1", "account_id": "5b10ac8d"}, level=1)
    assert status == "success", result
    assert result["message"] == "Assigned DEV-1 to account 5b10ac8d."

    result, status = jira("jira.assign_issue", {
        "issue_key": "DEV-1", "account_id": "none"}, level=1)
    assert status == "success", result
    assert result["message"] == "Unassigned DEV-1."

    assigned, unassigned = stub.sent("PUT", f"{API}/issue/DEV-1/assignee")
    assert assigned["json"] == {"accountId": "5b10ac8d"}
    assert unassigned["json"] == {"accountId": None}


# ---- failure, in words ---------------------------------------------

def test_jira_refusals_come_back_in_jiras_words(jira, stub):
    stub.on("GET", f"{API}/issue/DEV-404", {
        "errorMessages": ["Issue does not exist or you do not have permission to see it."],
        "errors": {}}, status=404)
    result, status = jira("jira.get_issue", {"issue_key": "DEV-404"})
    assert status == "error"
    assert result == {"error": "Jira answered 404: Issue does not exist or "
                               "you do not have permission to see it."}


def test_field_errors_are_named(jira, stub):
    stub.on("POST", f"{API}/issue", {
        "errorMessages": [], "errors": {"issuetype": "Specify a valid issue type"}},
        status=400)
    result, status = jira("jira.create_issue", {
        "project_key": "DEV", "summary": "x", "issue_type": "Nope"}, level=1)
    assert status == "error"
    assert result["error"] == "Jira answered 400: issuetype: Specify a valid issue type"


def test_an_unbound_connection_is_said_plainly(agents, stub):
    unbound = Invoker(agents["jira"], {})
    result, status = unbound("jira.list_issues")
    assert status == "error"
    assert result == {"error": "No Jira connection is bound to this agent."}


def test_an_incomplete_connection_names_what_is_missing(agents, stub):
    partial = Invoker(agents["jira"], {"jira__jira_connection": {
        "base_url": stub.url, "email": "", "api_token": ""}})
    result, status = partial("jira.list_issues")
    assert status == "error"
    assert result["error"] == (
        "The Jira connection is incomplete — missing: email, api_token.")


def test_a_bare_hostname_is_taken_as_https(agents, stub):
    """The one thing the stub cannot answer over https — but the URL the
    agent builds is visible in the connection failure."""
    bare = Invoker(agents["jira"], {"jira__jira_connection": {
        "base_url": "127.0.0.1:1", "email": "e", "api_token": "t"}})
    result, status = bare("jira.list_issues")
    assert status == "error"
    assert result["error"].startswith("Could not reach Jira:")


def test_writes_wait_for_the_chats_trust(jira, stub):
    """Writes are level 1: below the chat's trust and with nobody to
    approve, the executor refuses before anything is sent."""
    result, status = jira("jira.add_comment", {
        "issue_key": "DEV-1", "comment": "hi"}, level=0)
    assert status == "error"
    assert "approv" in result["error"].lower()
    assert stub.requests == []
