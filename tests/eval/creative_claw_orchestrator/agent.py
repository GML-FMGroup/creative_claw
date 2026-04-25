"""Root agent used by ADK evals."""

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator


_orchestrator = Orchestrator(
    session_service=InMemorySessionService(),
    artifact_service=InMemoryArtifactService(),
    expert_agents={},
)

root_agent = _orchestrator.agent
