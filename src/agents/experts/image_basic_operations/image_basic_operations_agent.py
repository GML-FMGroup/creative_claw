"""Image basic operations expert agent."""

from __future__ import annotations

from src.agents.experts.basic_operations_agent import BasicOperationsAgent
from src.agents.experts.image_basic_operations.tool import run_image_basic_operation


class ImageBasicOperationsAgent(BasicOperationsAgent):
    """Run one deterministic image basic operation inside the workspace."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the image basic operations expert."""
        super().__init__(
            name=name,
            description=description,
            operation_runner=run_image_basic_operation,
            results_key="image_basic_operation_results",
        )
