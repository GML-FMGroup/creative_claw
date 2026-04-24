import json
from pathlib import Path

from pydantic import BaseModel
from conf.path import CONF_ROOT
from src.runtime.expert_cards import discover_expert_cards


class ExpertAgentConfig(BaseModel):
    """
    Agent configuration model.
    """

    name: str  # Name of the expert agent
    enable: bool  # Whether the agent is enabled or not
    description: str
    parameters: str

    def __str__(self) -> str:
        return (
            (f"- **{self.name}**:\n"
            f"- function description: {self.description}\n"
            f"- `parameters`: {self.parameters}\n" 
            )
            if self.enable
            else f"**{self.name}** (Disabled)"
        )


def load_expert_configs(config_file_path: str | Path) -> list[ExpertAgentConfig]:
    """Load expert configurations from the JSON prompt metadata file."""
    config_path = Path(config_file_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    expert_agents = [
        ExpertAgentConfig(**agent) for agent in data.get("expert_agents", [])
    ]
    _apply_expert_cards(expert_agents)

    return expert_agents


def _apply_expert_cards(expert_agents: list[ExpertAgentConfig]) -> None:
    """Replace expert descriptions with `EXPERT.md` card descriptions when available."""
    cards = discover_expert_cards()
    for expert_agent in expert_agents:
        card = cards.get(expert_agent.name)
        if card is None:
            continue
        description = card.build_description()
        if description:
            expert_agent.description = description


EXPERTS_LIST: list[ExpertAgentConfig] = load_expert_configs(
    Path(CONF_ROOT) / "jsons" / "agent.json"
)
