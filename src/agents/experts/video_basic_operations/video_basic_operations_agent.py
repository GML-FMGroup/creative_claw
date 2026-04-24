"""Video basic operations expert agent."""

from __future__ import annotations

from src.agents.experts.basic_operations_agent import BasicOperationsAgent
from src.agents.experts.video_basic_operations.tool import run_video_basic_operation


class VideoBasicOperationsAgent(BasicOperationsAgent):
    """Run one deterministic video basic operation inside the workspace."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the video basic operations expert."""
        super().__init__(
            name=name,
            description=description,
            operation_runner=run_video_basic_operation,
            results_key="video_basic_operation_results",
        )
