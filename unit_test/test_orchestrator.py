import uuid
import asyncio
import sys
import json
import os
from dotenv import load_dotenv

from google.adk.sessions import InMemorySessionService
from google.adk.events import Event, EventActions
from google.genai.types import Part, Content

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conf.system import SYS_CONFIG
from src.agents.orchestrator.orchestrator_agent import Orchestrator
from src.logger import logger

# --- Constants ---
load_dotenv()
uid = 'test_orchestrator'
sid = f"test_orchestrator_{uuid.uuid4()}"
app_name = "test_orchestratir_app"

# --- Logging ---
logger.remove()
logger.level("DEBUG", color="<blue>")
logger.level("INFO", color="<white>")
logger.add(
    sys.stderr,
    level="INFO",
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)

# --- Session ---
session_service = InMemorySessionService()
internal = False # Use a internal session to store recursive chat of orchestrator or not

# --- Add execution event to session --
# this is used to simulate the tool execution result
async def add_event(text:str, state_delta:dict):
    current_session = await session_service.get_session(app_name=app_name, user_id=uid, session_id=sid)
    
    event = Event(
        author='api_service', 
        content=Content(role='model', parts=[Part(text=text)]),
        actions=EventActions(state_delta=state_delta))
    await session_service.append_event(current_session, event)

# --- test function --
async def test(prompt: str):
    agent = Orchestrator(session_service, app_name=app_name, internal=internal)

    await session_service.create_session(
        app_name=app_name, user_id=uid, session_id=sid, state={}
    )

    # firstly generate whole plan for all steps
    await agent.initialize_state(prompt, sid, uid)
    plan = await agent.generate_plan(global_plan=True)

    current_session = await session_service.get_session(app_name=app_name, user_id=uid, session_id=sid)
    logger.info(f"session.state: {json.dumps(current_session.state)}")

    round = 0
    while round<5:
        plan = await agent.generate_plan(global_plan=False)
        logger.info(f"Round{round} plan: \n {json.dumps(plan)}")

        # simulate tool execution result
        output_path = f'{round}.png'
        text=f"Image generated successfully. Output path: {output_path}"
        state_delta={"agent_output": output_path}

        await add_event(text, state_delta)
        if internal: await agent.update_internal_session(text, state_delta)

        current_session = await session_service.get_session(app_name=app_name, user_id=uid, session_id=sid)
        logger.info(f"session.state: {json.dumps(current_session.state)}")

        decision = plan.get('next_agent')
        if decision.lower() in ['null', 'finish']:
            logger.info(f"Task done, conversation terminating...")
            break

# --- main function ---
async def main():
    logger.info("Start Conversation. Type 'exit' to quit.")
    while True:
        try:
            prompt = input("You: ")
            if prompt.lower() == "exit":
                logger.info("Existing conversation")
                break
            await test(prompt)
        except (KeyboardInterrupt, EOFError):
            logger.info("\nExiting conversation.")
            break

if __name__ == '__main__':
    asyncio.run(test("Generate an image of a cat"))
    pass
