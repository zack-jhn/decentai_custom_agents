"""The agents run the way production runs them — in a worker spawned
from their own environment, over the worker protocol — against a stub
Jira or GitHub on loopback. Needs the DecentAI platform on the path:

    PYTHONPATH=/path/to/decentai python -m pytest tests -q
"""

import asyncio
from pathlib import Path

import pytest
import yaml

from ai_runtime.agents import AgentEnvironment, InstalledAgent
from ai_runtime.execution.executor import FunctionExecutor
from contracts.agent_manifest import load_manifest
from sim.resources import InMemoryResourceProvider

from tests.stub import StubServer

ROOT = Path(__file__).resolve().parents[1]

# One shared environment for every catalog agent, holding the union of
# their declared dependencies — cached across runs. After changing any
# agent's dependencies, delete tests/.workerenv so the next run rebuilds.
WORKERENV_DIR = Path(__file__).resolve().parent / ".workerenv"


@pytest.fixture(scope="session")
def agents():
    """Every catalog agent as a real InstalledAgent, by its own id."""
    catalog = yaml.safe_load(
        (ROOT / "decentai-agents.yaml").read_text(encoding="utf-8"))

    entries = []
    for entry in catalog["agents"]:
        folder = ROOT / entry["path"]
        manifest, errors = load_manifest(folder / "manifest.yaml")
        assert errors == [], f"{entry['id']}: {errors}"
        assert manifest.agent_id == entry["id"], (
            f"catalog says {entry['id']}, manifest says {manifest.agent_id}")
        entries.append((folder, manifest))

    environment = AgentEnvironment(WORKERENV_DIR)
    if not environment.exists():
        dependencies = [d for _, m in entries for d in m.dependencies]
        errors = environment.build(dependencies)
        assert errors == [], errors

    return {
        manifest.agent_id: InstalledAgent(
            f"fixture:{manifest.agent_id}", manifest, folder, environment)
        for folder, manifest in entries
    }


@pytest.fixture()
def stub():
    """A fresh stub service per test."""
    server = StubServer().start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _reap_worker_pools():
    """Workers spawned during a test die with it."""
    yield
    from ai_runtime.agents.worker_pool import WorkerPool

    WorkerPool.terminate_all()


class Invoker:
    """One agent's functions, invoked through the real executor with the
    given bound secrets. ``name`` is ``tool.function``."""

    def __init__(self, agent, secrets):
        self.agent = agent
        self.executor = FunctionExecutor(
            provider=InMemoryResourceProvider(secrets=secrets))

    def __call__(self, name, inputs=None, level=0):
        return asyncio.run(self.executor.invoke(
            self.agent, f"{self.agent.manifest.agent_id}.{name}",
            dict(inputs or {}), chat_level=level))
