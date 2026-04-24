"""Audio basic operations expert agent."""

from __future__ import annotations

from src.agents.experts.basic_operations_agent import BasicOperationsAgent
from src.agents.experts.audio_basic_operations.tool import run_audio_basic_operation


class AudioBasicOperationsAgent(BasicOperationsAgent):
    """Run one deterministic audio basic operation inside the workspace."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the audio basic operations expert."""
        super().__init__(
            name=name,
            description=description,
            operation_runner=run_audio_basic_operation,
            results_key="audio_basic_operation_results",
        )
