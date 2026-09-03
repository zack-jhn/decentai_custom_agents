"""The Jira agent for task management."""

from decentai_sdk.base import AgentBase
from .tools.jira_tool import JiraTool

class JiraAgent(AgentBase):
    def tools(self):
        return [JiraTool(self)]