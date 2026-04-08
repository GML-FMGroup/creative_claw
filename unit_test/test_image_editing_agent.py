import asyncio
from loguru import logger
import sys
import os
import json

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agents.experts.image_editing.image_editing_agent import ImageEditingAgent
from src.agents.experts import (
    prompt_enhancement_agent,
)


# --- Constants ---
APP_NAME = "editing_test_app"
USER_ID = "test_user"
SESSION_ID = "test_session"

# --- Configure Logging ---
logger.remove()
logger.level("DEBUG", color="<blue>")
logger.level("INFO", color="<white>")
logger.add(
    sys.stderr,
    level="INFO",
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)


# --- Create the custom agent instance ---
editing_agent = ImageEditingAgent(
    name="ImageEditingAgent",
    prompt_enhancement_agent=prompt_enhancement_agent,
)


# --- Setup Runner and Session ---
async def setup_session_and_runner(initial_state: dict = {}):
    """Sets up the session service and runner for the agent."""
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID, state=initial_state
    )
    logger.info(f"Initial session state: {session.state}")
    runner = Runner(
        agent=editing_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )
    return session_service, runner


# --- Function to Interact with the Agent ---
async def call_agent_async(user_input: str, image_path: str, editing_function: str):
    """Sends user input to the agent and runs the workflow."""
    session_service, runner = await setup_session_and_runner(
        initial_state={
            "prompt_to_enhance": user_input,
            "image_editing_base_image_path": image_path,
            "image_editing_function": editing_function
            }
    )

    content = types.Content(
        role="user", parts=[types.Part(text=f"Editing an image about: {user_input}")]
    )
    events = runner.run_async(
        user_id=USER_ID, session_id=SESSION_ID, new_message=content
    )

    final_response = "No final response captured."
    async for event in events:
        logger.debug(f"Event: {event}")
        if event.is_final_response() and event.content and event.content.parts:
            logger.info(
                f"Potential final response from [{event.author}]: {event.content.parts[0].text}"
            )
            final_response = event.content.parts[0].text

    logger.info("\n--- Agent Interaction Result ---")
    logger.info(f"Agent Final Response: {final_response}")

    final_session = await session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID
    )
    logger.info("Final Session State:")

    logger.info(json.dumps(final_session.state, indent=2))  # type: ignore
    logger.info("-------------------------------\n")


# --- Main execution loop ---
async def main() -> None:
    """Main function to run the command-line chat interface."""
    logger.info("Start Conversation. Type 'exit' to quit.")
    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() == "exit":
                logger.info("Exiting conversation.")
                break
            await call_agent_async(user_input)
        except (KeyboardInterrupt, EOFError):
            logger.info("\nExiting conversation.")
            break


if __name__ == "__main__":
    asyncio.run(call_agent_async(
        user_input="Change the model's top to blue",
        image_path="/data/yanglingxiao/code/model2.png",
        editing_function="description_edit"
    ))
