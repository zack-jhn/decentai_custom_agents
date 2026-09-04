"""The GitHub agent against a stub GitHub API."""

import pytest

from tests.conftest import Invoker


@pytest.fixture()
def github(agents, stub):
    return Invoker(agents["github"], {"github__github_token": {
        "token": "token-abc", "api_base_url": stub.url}})


def test_list_prs_sends_the_token_and_reads_the_essentials(github, stub):
    stub.on("GET", "/repos/octo/app/pulls", [
        {"number": 12, "title": "Add login", "state": "open", "draft": True,
         "user": {"login": "ada"}, "html_url": "https://github.com/octo/app/pull/12"},
    ])

    result, status = github("github.list_prs", {"owner": "octo", "repo": "app"})

    assert status == "success", result
    assert result == {"prs": [{
        "number": 12, "title": "Add login", "state": "open", "author": "ada",
        "draft": True, "url": "https://github.com/octo/app/pull/12"}]}
    sent = stub.sent("GET", "/repos/octo/app/pulls")[0]
    assert sent["headers"]["authorization"] == "Bearer token-abc"
    assert sent["headers"]["x-github-api-version"] == "2022-11-28"
    assert sent["query"] == {"state": "open", "per_page": "30",
                             "sort": "updated", "direction": "desc"}


def test_list_prs_takes_a_state_and_a_limit(github, stub):
    stub.on("GET", "/repos/octo/app/pulls", [])
    result, status = github("github.list_prs", {
        "owner": "octo", "repo": "app", "state": "closed", "limit": 5})
    assert status == "success", result
    assert result == {"prs": []}
    query = stub.sent("GET", "/repos/octo/app/pulls")[0]["query"]
    assert query["state"] == "closed" and query["per_page"] == "5"


def test_get_pr_diff_asks_for_a_diff(github, stub):
    diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
    stub.on("GET", "/repos/octo/app/pulls/12", diff, content_type="text/plain")

    result, status = github("github.get_pr_diff", {
        "owner": "octo", "repo": "app", "pr_number": 12})

    assert status == "success", result
    assert result == {"diff_content": diff, "truncated": False}
    assert stub.sent("GET", "/repos/octo/app/pulls/12")[0]["headers"]["accept"] == (
        "application/vnd.github.diff")


def test_a_huge_diff_is_cut_and_says_so(github, stub):
    stub.on("GET", "/repos/octo/app/pulls/13", "+line\n" * 5000,
            content_type="text/plain")
    result, status = github("github.get_pr_diff", {
        "owner": "octo", "repo": "app", "pr_number": 13})
    assert status == "success", result
    assert result["truncated"] is True
    assert result["diff_content"].endswith("... (diff truncated)")
    assert len(result["diff_content"]) < 20100


def test_create_issue_sends_only_what_was_given(github, stub):
    stub.on("POST", "/repos/octo/app/issues", {
        "number": 7, "html_url": "https://github.com/octo/app/issues/7"}, status=201)

    result, status = github("github.create_issue", {
        "owner": "octo", "repo": "app", "title": "Crash on start"}, level=1)
    assert status == "success", result
    assert result == {"number": 7, "issue_url": "https://github.com/octo/app/issues/7"}

    github("github.create_issue", {
        "owner": "octo", "repo": "app", "title": "Slow", "body": "Details",
        "labels": ["perf"]}, level=1)

    bare, full = stub.sent("POST", "/repos/octo/app/issues")
    assert bare["json"] == {"title": "Crash on start"}
    assert full["json"] == {"title": "Slow", "body": "Details", "labels": ["perf"]}


def test_github_refusals_come_back_in_githubs_words(github, stub):
    stub.on("POST", "/repos/octo/app/issues", {
        "message": "Validation Failed",
        "errors": [{"resource": "Issue", "field": "title", "code": "missing_field"}],
    }, status=422)
    result, status = github("github.create_issue", {
        "owner": "octo", "repo": "app", "title": "x"}, level=1)
    assert status == "error"
    assert result == {"error": "GitHub answered 422: Validation Failed (missing_field)"}


def test_a_missing_repository_is_a_readable_error(github, stub):
    result, status = github("github.list_prs", {"owner": "octo", "repo": "gone"})
    assert status == "error"
    assert result["error"] == "GitHub answered 404: Not Found"


def test_an_unbound_token_is_said_plainly(agents, stub):
    unbound = Invoker(agents["github"], {})
    result, status = unbound("github.list_prs", {"owner": "o", "repo": "r"})
    assert status == "error"
    assert result == {"error": "No GitHub token is bound to this agent."}


def test_creating_an_issue_waits_for_the_chats_trust(github, stub):
    result, status = github("github.create_issue", {
        "owner": "octo", "repo": "app", "title": "x"}, level=0)
    assert status == "error"
    assert "approv" in result["error"].lower()
    assert stub.requests == []
