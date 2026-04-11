"""Compatibility helpers for ADK integration details.

This module intentionally centralizes the few places where Creative Claw still
touches ADK private metadata. If ADK later exposes a public API for agent
origin metadata, only this file should need to change.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import BaseAgent


def annotate_agent_origin(agent: BaseAgent, *, app_name: str, origin_path: Path) -> BaseAgent:
    """Attach explicit origin metadata to one programmatically created ADK agent.

    ADK's `Runner` prefers `_adk_origin_app_name` and `_adk_origin_path` when
    inferring app alignment. Creative Claw creates experts directly in code
    instead of loading them through ADK's AgentLoader, so we mirror that loader
    behavior here in one contained place.
    """
    setattr(agent, "_adk_origin_app_name", app_name)
    setattr(agent, "_adk_origin_path", origin_path)
    return agent
