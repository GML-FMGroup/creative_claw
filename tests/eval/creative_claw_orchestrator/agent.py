"""Root agent used by ADK evals."""

import sys
import struct
import zlib
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from google.adk.artifacts import InMemoryArtifactService
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.runtime.workspace import workspace_root


_EVAL_PRODUCT_SIZE = 320


class _EvalStubExpertAgent(BaseAgent):
    """Return deterministic expert output for routing-only ADK eval cases."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name, description="Eval-only stub expert.")

    async def _run_async_impl(self, ctx):
        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "current_output": {
                        "status": "success",
                        "message": f"{self.name} eval stub completed.",
                        "output_text": f"{self.name} was selected for this non-Design eval path.",
                    }
                }
            ),
        )


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Return one PNG chunk with length and CRC."""
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _build_eval_product_png() -> bytes:
    """Build a valid, sufficiently large PNG product placeholder."""
    width = height = _EVAL_PRODUCT_SIZE
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            inside_pack = 72 <= x <= 248 and 46 <= y <= 286
            inside_label = 98 <= x <= 222 and 124 <= y <= 190
            if inside_label:
                rgb = (249, 250, 252)
            elif inside_pack:
                rgb = (38 + y // 12, 92 + x // 18, 120 + y // 16)
            else:
                rgb = (238, 241, 235)
            row.extend(rgb)
        rows.append(bytes(row))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    compressed = zlib.compress(b"".join(rows), level=9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def _ensure_eval_product_fixture() -> None:
    """Create the workspace product image used by reference-file eval cases."""
    target = workspace_root() / "input" / "eval_product.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_build_eval_product_png())


def _ensure_eval_ppt_source_fixture() -> None:
    """Create the workspace source document used by PPT eval cases."""
    target = workspace_root() / "input" / "eval_q1_business_notes.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            [
                "# Q1 Business Notes",
                "- Q1 revenue grew 20% year over year, mainly from enterprise renewals.",
                "- Customer retention improved by 6 percentage points after onboarding changes.",
                "- Cloud infrastructure cost increased 14%, pressuring gross margin.",
                "- The leadership decision is whether to fund retention automation before new acquisition spend.",
            ]
        ),
        encoding="utf-8",
    )


_ensure_eval_product_fixture()
_ensure_eval_ppt_source_fixture()


_orchestrator = Orchestrator(
    session_service=InMemorySessionService(),
    artifact_service=InMemoryArtifactService(),
    expert_agents={
        "ImageGenerationAgent": _EvalStubExpertAgent(name="ImageGenerationAgent"),
    },
)

root_agent = _orchestrator.agent
