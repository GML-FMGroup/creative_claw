"""Tool helpers for atomic text transformation."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part

from conf.llm import build_llm, resolve_llm_model_name
from src.logger import logger

_SUPPORTED_TEXT_TRANSFORM_MODES = {
    "rewrite",
    "expand",
    "compress",
    "translate",
    "structure",
    "title",
    "script",
}


def normalize_text_transform_mode(raw_value: str) -> str:
    """Return one supported text transform mode or a safe default."""
    value = str(raw_value or "").strip().lower()
    return value if value in _SUPPORTED_TEXT_TRANSFORM_MODES else "rewrite"


def _build_transform_request(
    *,
    input_text: str,
    mode: str,
    target_language: str = "",
    style: str = "",
    constraints: str = "",
) -> str:
    """Build one atomic text-transform request for the LLM."""
    mode_instructions = {
        "rewrite": "Rewrite the text clearly while preserving the original meaning.",
        "expand": "Expand the text with more detail while keeping the same intent.",
        "compress": "Compress the text aggressively while preserving the core meaning.",
        "translate": "Translate the text faithfully into the requested target language.",
        "structure": "Re-structure the text into a clearer format with logical sections.",
        "title": "Generate a concise and high-quality title from the text.",
        "script": "Transform the text into a usable script with natural flow.",
    }
    request_lines = [
        "Perform exactly one atomic text transformation.",
        f"Mode: {mode}",
        mode_instructions.get(mode, mode_instructions["rewrite"]),
    ]
    if target_language.strip():
        request_lines.append(f"Target language: {target_language.strip()}")
    if style.strip():
        request_lines.append(f"Style requirement: {style.strip()}")
    if constraints.strip():
        request_lines.append(f"Constraints: {constraints.strip()}")
    request_lines.extend(
        [
            "Return only the transformed result.",
            "Do not explain your work.",
            "",
            "Source text:",
            input_text,
        ]
    )
    return "\n".join(request_lines)


async def transform_text_tool(
    ctx: InvocationContext,
    *,
    input_text: str,
    mode: str,
    target_language: str = "",
    style: str = "",
    constraints: str = "",
) -> dict[str, str]:
    """Run one atomic text transformation with the configured LLM."""
    normalized_mode = normalize_text_transform_mode(mode)
    request_text = _build_transform_request(
        input_text=input_text,
        mode=normalized_mode,
        target_language=target_language,
        style=style,
        constraints=constraints,
    )

    def before_model_callback(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> None:
        """Inject the current text-transform request into the LLM call."""
        llm_request.contents.append(Content(role="user", parts=[Part(text=request_text)]))

    llm = LlmAgent(
        name="TextTransformToolAgent",
        model=build_llm(),
        instruction=(
            "You are an expert text transformer. "
            "Do exactly one requested transformation and return only the result."
        ),
        include_contents="none",
        before_model_callback=before_model_callback,
    )

    try:
        output_text = ""
        async for event in llm.run_async(ctx):
            if event.is_final_response() and event.content and event.content.parts:
                generated_text = next((part.text for part in event.content.parts if part.text), None)
                if generated_text:
                    output_text = generated_text.strip()

        if not output_text:
            return {
                "status": "error",
                "message": "Text transform returned empty text.",
                "mode": normalized_mode,
                "provider": "google_adk",
                "model_name": resolve_llm_model_name(),
            }

        return {
            "status": "success",
            "message": output_text,
            "mode": normalized_mode,
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "text transform failed: mode={} error_type={} error={!r}",
            normalized_mode,
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"Text transform failed: {type(exc).__name__}: {exc}",
            "mode": normalized_mode,
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
        }
