"""Compatibility wrapper for the image-to-prompt expert."""

from __future__ import annotations

from google.adk.agents.invocation_context import InvocationContext

from src.agents.experts.image_understanding.tool import image_to_text_tool


async def image_to_prompt_tool(ctx: InvocationContext, input_path: str) -> dict:
    """Generate one reverse prompt by delegating to image understanding prompt mode."""
    result = await image_to_text_tool(ctx, input_path, mode="prompt")
    if result.get("status") != "success":
        return result

    return {
        "status": "success",
        "message": str(result.get("analysis_text", "")).strip() or str(result.get("message", "")).strip(),
        "provider": str(result.get("provider", "")).strip(),
        "model_name": str(result.get("model_name", "")).strip(),
    }
