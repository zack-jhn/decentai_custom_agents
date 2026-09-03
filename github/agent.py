"""The GitHub agent for code management."""

from decentai_sdk.base import AgentBase
from .tools.github_tool import GitHubTool

class GitHubAgent(AgentBase):
    def tools(self):
        return [GitHubTool(self)]