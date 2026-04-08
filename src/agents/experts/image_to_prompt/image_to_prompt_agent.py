"""Expert agent that reverse-engineers image generation prompts."""

from typing import Any, AsyncGenerator, Dict
from typing_extensions import override
import asyncio

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai.types import Content, Part

from src.logger import logger
from src.agents.experts.image_to_prompt.tool import image_to_prompt_tool


class ImageToPromptAgent(BaseAgent):
    """Generate prompt-like descriptions from one or more input images."""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the image-to-prompt expert."""
        super().__init__(name=name, description=description)

    def format_event(
        self,
        content_text: str | None = None,
        state_delta: Dict[str, Any] | None = None,
    ) -> Event:
        """Build one ADK event with optional content and state updates."""
        event = Event(author=self.name)
        if state_delta:
            event.actions = EventActions(state_delta=state_delta)
        if content_text:
            event.content = Content(role="model", parts=[Part(text=content_text)])
        return event

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Reverse prompts for images saved in the current session state.

        Args:
            ctx: Current ADK invocation context.

        Yields:
            ADK events containing the generated prompt text and state deltas.
        """
        current_parameters = ctx.session.state.get("current_parameters", {})

        if "input_name" not in current_parameters:
            error_text = f"Missing parameters provided to {self.name}, must include: input_name"
            current_output = {"status": "error", "message": error_text}
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        input_name = current_parameters["input_name"]
        if isinstance(input_name, str):
            input_name = [input_name]

        tasks = [image_to_prompt_tool(ctx, img_name) for img_name in input_name]
        results = await asyncio.gather(*tasks)

        success_message_list = []
        error_message_list = []
        count = len(input_name)
        for name, result in zip(input_name, results):
            if result["status"] == "success":
                success_message_list.append(f"Image {name} prompt:\n{result['message']}\n---\n")
            elif result["status"] == "error":
                error_message_list.append(
                    f"Image {name} prompt generation failed: {result['message']}\n---\n"
                )

        if len(error_message_list) == count:
            error_text = f"Failed to reverse prompts for all {count} image(s):\n\n" + "\n".join(
                error_message_list
            )
            current_output = {
                "author": self.name,
                "status": "error",
                "message": error_text,
                "message_for_user": error_text,
                "output_text": "",
            }
            logger.error(error_text)
            yield self.format_event(state_delta={"image_to_prompt_results": error_text})
            yield self.format_event(error_text, state_delta={"current_output": current_output})
            return

        message = (
            f"Finished reverse prompting {count} image(s), "
            f"with {len(success_message_list)} success(es)."
        )
        output_text = message + "\n\n" + "\n".join(success_message_list + error_message_list)

        current_output = {
            "author": self.name,
            "status": "success",
            "message": message,
            "message_for_user": message,
            "output_text": output_text,
        }

        yield self.format_event(state_delta={"image_to_prompt_results": output_text})
        yield self.format_event(output_text, state_delta={"current_output": current_output})
        return
