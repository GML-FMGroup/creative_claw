from typing import AsyncGenerator, List, Optional, Any, Dict
import json, uuid
import re

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.callback_context import CallbackContext
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.adk.models import LlmRequest
from google.genai.types import Content, Part


from src.logger import logger
from google.adk.agents import LlmAgent
from conf.system import SYS_CONFIG
from conf.agent import experts_list

avaliable_agents: str = '\n'.join([str(expert) for expert in experts_list if expert.enable])


def clean_and_parse_json(json_string: str) -> Dict[str, Any]:
    cleaned_string = re.sub(r'^```(json)?\s*|\s*```$', '', json_string.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned_string)
    except json.JSONDecodeError:
        logger.error(f"JSON decoding failed, original string: '{json_string}'")
        return {}
    


async def orchestrator_before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """
    orchestrator_agent.planner and checker's before model callback
    they will append <image information> and <execution history information> to llm_request
    using callback can avoid adding these long series of information to context
    """
    new_artifacts = callback_context.state.get('new_artifacts')

    # add new artifact to context
    if new_artifacts and len(new_artifacts)>0:
        artifact_parts = [Part(text=f"The following are the images from new input or the previous steps of execution: \n")]
        for i, art in enumerate(new_artifacts):
            artifact_parts.append(Part(text=f"This is image {i+1}, name: {art['name']}, description: {art.get('description')}\n")) 

            art_part = await callback_context._invocation_context.artifact_service.load_artifact(
                app_name=callback_context.state['app_name'],
                user_id=callback_context.state['uid'],
                session_id=callback_context.state['sid'],
                filename=art['name']
            )
            artifact_parts.append(art_part)

        llm_request.contents.append(Content(role='user', parts=artifact_parts))

    # add auxiliary text
    step = callback_context.state.get("step")
    aux_text = f"# Number of executed steps: {step} \n\n"

    # add input original image information
    input_artifacts = callback_context.state.get("input_artifacts",[])
    if len(input_artifacts)>0:
        art_list = []
        for i, art in enumerate(input_artifacts):
            art_list.append(f"Original input image {i+1}: name: {art['name']}, description: {art['description']}")
        aux_text = aux_text + "# The original image input by the user for the current task:\n"+'\n'.join(art_list) + '\n\n'
    else:
        aux_text = aux_text + "# The current task user did not input any image\n\n"
        
    # add previous step execution information
    summary_history = callback_context.state.get("summary_history",[])
    message_history = callback_context.state.get("message_history",[])
    if len(summary_history) and len(message_history):
        sum_list = []
        for i, (summary, message) in enumerate(zip(summary_history, message_history)):
            sum_list.append(f"**step {i+1}**: target: {summary}, execution result: {message}\n")
        aux_text = aux_text + "# Information for all previous execution steps:\n" + '\n'.join(sum_list) + '\n\n'

    # add information of artifacts generated in previous steps
    artifacts_history = callback_context.state.get("artifacts_history", [])
    if len(artifacts_history)>0:
        art_text_list = []
        for step, art_list in enumerate(artifacts_history):
            if len(art_list)==0:
                art_text_list.append(f"**step{step+1}**: This step did not generate any image")
                continue

            art_text = f"**step{step+1}**:  "
            for j, art in enumerate(art_list):
                art_text = art_text + f"generated image {j+1}: name: {art['name']}, description: {art.get('description')}.  "
            art_text_list.append(art_text)
        
        aux_text = aux_text + "# Output file status for all previous execution steps:\n"+'\n'.join(art_text_list) + '\n\n'
    
    if aux_text:
        llm_request.contents.append(Content(role='user', parts=[Part(text = aux_text)]))

    return None


class OrchestratorAgent(BaseAgent):
    """
    The orchestrator agent that generate plan for input request by using a sequence of sub agent
    """
    model_config = {"arbitrary_types_allowed": True}
    max_iterations: int
    planner: LlmAgent

    def __init__(
        self,
        name,
        description,
        llm_model: str = '',
        max_iterations: int = 3,
    ):
        if not llm_model:
            llm_model = SYS_CONFIG.llm_model
        logger.info(f"OrchestratorAgent: using llm: {llm_model}")

        planner = LlmAgent(
            name="PlannerAgent",
            model=llm_model,
            description='Analyze input request, output a plan in json format in order to successive execution.',
            instruction=ORCHESTRATOR_INSTRUCTION + avaliable_agents,
            before_model_callback=orchestrator_before_model_callback,
        )

        critic = LlmAgent(
            name="CriticAgent", 
            model=llm_model, 
            description="check the plan and output optimization instruction",
            instruction=CRITIC_INSTRUCTION + avaliable_agents,
            output_key='instruction',
            before_model_callback=orchestrator_before_model_callback,
        )

        checker = CheckStatusEscalate(name="StopChecker")

        sub_agents = [planner, critic, checker]

        super().__init__(
            name = name,
            description = description,
            sub_agents = sub_agents,
            max_iterations = max_iterations,
            planner = planner,
        )
        

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """Run the orchestrator agent
        if self.max_iterations<=0, this method will just call the planner agent
        otherwise, it will recursively call planner and critic to form a plan

        Args:
            ctx (InvocationContext): The invocation context for the agent.
        Yields:
            Event: The events generated by the sequential agent during the plan generation process.

        """
        if self.max_iterations<=0:
            async for event in self.planner.run_async(ctx):
                yield event
            return
        else:
            times_looped = 0
            while times_looped<self.max_iterations:
                for agent in self.sub_agents:
                    async for event in agent.run_async(ctx):
                        yield event
                        if event.actions.escalate:
                            return
                times_looped += 1
            return

class Orchestrator:
    """ Call the OrchestratorAgent to form a plan
    This class contains session service to store the chat history
    """
    def __init__(self,
        session_service: InMemorySessionService,
        artifact_service: InMemoryArtifactService,
        app_name: str = 'default_app_name',
        llm_model: str = '',
        max_iter:int = 4,
        internal: bool = True,
    ):
        """ In order to reduce the length of overall chat history,
        Orchestrator provides a choice to store the recursive generation history in a local internal session service. Only the final plan will be stored in external session service (main session)

        Args:
            external_session_service: the main session used for the whole project.
            llm_model: name of model
            max_iter: the number of iterations of recursive generation
            internal: use internal session service or not
            internal_session_service: provide internal session service
        
        """
        self.app_name = app_name
        self.max_iter = max_iter
        self.internal = internal
        self.session_service = session_service
        self.artifact_service = artifact_service

        self.uid:str = None
        self.sid:str = None

        if not llm_model:
            llm_model = SYS_CONFIG.llm_model
        logger.info(f"OrchestratorAgent: using llm: {llm_model}")

        self.orchestrator_agent = OrchestratorAgent(
            name='OrchestratorAgent',
            description="""Generate global and step-by-step plan for user's request""",
            llm_model=llm_model,
            max_iterations=max_iter,
        )

        self.runner = Runner(
            agent=self.orchestrator_agent,
            app_name=self.app_name,
            session_service=self.session_service,
            artifact_service=self.artifact_service
        )
        
    async def run_agent_and_log_events(
        self, user_id: str, session_id: str, new_message: Optional[Content] = None
    ) -> str:
        """
        call the runner to run OrchestratorAgent
        """
        final_response_text_list = []
        async for event in self.runner.run_async(user_id=user_id, session_id=session_id, new_message=new_message):
            logger.debug(f"uid: {user_id}, sid: {session_id}, Event: {event.model_dump_json(indent=2, exclude_none=True)}")
            if event.is_final_response() and event.content and event.content.parts:
                text_part = next((part.text for part in event.content.parts if part.text), None)
                if text_part:
                    final_response_text = text_part
                    logger.info(f"uid: {user_id}, sid: {session_id}, [{self.runner.agent.name}], text: '{final_response_text}'")
                    final_response_text_list.append(final_response_text)
        if self.max_iter>0:
            return final_response_text_list[-2] if len(final_response_text_list)>=2 else "" 
        else:
            return final_response_text_list[-1]

    async def create_internal_session(self) -> str:
        """
        copy the main session to form an internal session,
        the internal session will be used in internal roleplay
        """
        internal_sid = f"internal_orchestrator_{uuid.uuid4()}"
        current_external_session = await self.session_service.get_session(
            app_name=self.app_name, user_id=self.uid, session_id=self.sid
        )

        await self.session_service.create_session(
            app_name=self.app_name, user_id=self.uid, session_id=internal_sid, state=current_external_session.state
        )
        current_internal_session = await self.session_service.get_session(
            app_name=self.app_name, user_id=self.uid, session_id=internal_sid
        )
        # Copy events.
        for event in current_external_session.events:
            await self.session_service.append_event(current_internal_session, event)
        
        return internal_sid



    async def generate_plan(self, global_plan: bool=False) -> Dict:
        """ generate a plan based on current state
        and write the generated plan as a new event to external session service

        Args:
            global_plan: generate whole plan for all steps, or just generate one step
        """
        # If self.internal is enabled, create a new internal session.
        if self.internal:
            sid = await self.create_internal_session()
        else:
            sid = self.sid


        if global_plan:
            new_message = Content(role='user', parts=[Part(text="Please generate all task steps at once, taking into account the input-output dependencies between the previous and subsequent steps.")])
        else:
            new_message = Content(role='user', parts=[Part(text="Generate the next single steps based on the original task and current state. You only must focus on the overall task and the current state.")])

        # append new message to external session
        if self.internal:
            current_external_session = await self.session_service.get_session(app_name=self.app_name, user_id=self.uid, session_id=self.sid)
            await self.session_service.append_event(
                session=current_external_session, 
                event=Event(author='api_server', content=new_message))

        orchestrator_decision_str = await self.run_agent_and_log_events(self.uid, sid, new_message=new_message)
        plan = clean_and_parse_json(orchestrator_decision_str)

        print("plan: ", plan)

        if global_plan:
            if isinstance(plan, dict): plan = [plan]
            decision_list = [step.get("next_agent") for step in plan]
            summary_list = [step.get("summary", "<failed to get the summary of current step>") for step in plan]
            final_summary = "All steps as follows: "+','.join(summary_list)
            plan_event = Event(
                author="api_server", 
                content=Content(role='model', parts=[Part(text=f"Successfully generated plans for all steps:\n {json.dumps(plan, ensure_ascii=False)}\n\nNext, you can use it as a reference to gradually generate single step plans")]), 
                actions=EventActions(state_delta={"global_plan": plan}))
        else:
            decision = plan.get("next_agent")
            final_summary = plan.get("summary", "<failed to get the summary of current step>")

            plan_event = Event(
                author="api_server", 
                content=Content(role='model', parts=[Part(text=f"Successfully generated single step plan in the current state:\n {json.dumps(plan, ensure_ascii=False)}")]),
                actions=EventActions(state_delta={"current_plan": plan}))

        # write plan to state
        external_session = await self.session_service.get_session(app_name=self.app_name, user_id=self.uid, session_id=self.sid)
        await self.session_service.append_event(external_session, plan_event)
        logger.info(f"Orchestrator planning finished. Summary: {final_summary}")

        return plan, final_summary


class CheckStatusEscalate(BaseAgent):
    """Check if the recursive generation can be terminated
    """
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """if there is 'NONE' in session.state['instrunction'], the process will be terminated"""
        status = ctx.session.state.get("instruction")
        should_stop = (status == "NONE") or ("NONE" in status)
        yield Event(author=self.name, actions=EventActions(escalate=should_stop))


CRITIC_INSTRUCTION = """
    You need to collaborate with an art creation planning AI to improve and optimize the task step planning it generates, and each step of the planning will be executed by an expert agent. The output of the planning AI is a JSON object as follows:
    ```json
    {{
        "next_agent": "AgentName",
        "parameters": {{
        "param1_for_agent": "value1"
        }},
        "summary": "A brief summary of your current decision will be presented to the user."
    }}
    ```

    # The task steps output by the planning AI may include two situations:**
    1. Plan all the steps of the overall task, the JSON output is a list containing all the steps of the task
    2. Plan the next single step under the current state, the JSON output is a dict.


    # Input information
    During each planning process, you may be provided with the following reference information:
    1. **user's request**: including text description and input images
    2. **executed steps**: steps that have already been executed.
    3. **current files/images**: images that needs to be operated in the current step
    4. **historical output information**: output image information for each step that has been executed before
    5. **historical message**: summary of tasks for each step that has been executed before


    ** Task Requirements **
    1.You must carefully check the original task requirements entered by the user: {user_prompt} and the plan generated by planner, check if the agent and parameters it calls are correct
    2.You must check if the dependencies before and after all steps are correct. Check the file names of each input and output step

    ** Output Format **
    Suggestions for improving the guidance of your output in string form:
    If there are no issues with the current plan, output 'NONE'
    If there are problems with the current plan, provide improvement suggestions and provide detailed explanations of the issues and improvement methods. At the same time, you need to indicate whether the current optimization is global planning or single step planning

    **List of expert agents that can be used in planning:**\n\n
"""


ORCHESTRATOR_INSTRUCTION = """
    You are the overall commander AI of an art creation pipeline. Your task is to continuously analyze the current task execution status in a loop and decide which expert agent needs to be called for the next step (after which your decision will be handed over to the expert for execution and the execution result will be returned for the next round of decision-making). In addition, you may also be asked to output all task steps at once at the beginning.

    # Input information
    During each planning process, you may be provided with the following reference information:
    1. **user's request**: including text description and input images
    2. **executed steps**: steps that have already been executed.
    3. **current files/images**: images that needs to be operated in the current step
    4. **historical output information**: output image information for each step that has been executed before
    5. **historical message**: summary of tasks for each step that has been executed before

    # Output format
    **If you are asked to output a single step plan, your only output must be a JSON object:

    **JSON format:**
    ```json
    {{
        "next_agent": "AgentName",
        "parameters": {{
        "param1_for_agent": "value1"
        }},
        "summary": "A brief summary of your current decision, stating what needs to be done, will be presented to the user."
    }}
    ```

    **If you are asked to output all steps at once, the JSON object you output is a list:
    ```json
    [
        {{
            "next_agent": "AgentName",
            "parameters": {{"param_for_agent": "value"}},
            "summary": "summary of step1"
        }},
        {{
            "next_agent": "AgentName",
            "parameters": {{"param_for_agent": "value"}},
            "summary": "summary of step2"
        }},
        ...
    ]
    ```

    # Attention:
    1.  **End Signal**: If you determine that current task has been completely completed, you must set the value of 'next_agent' to 'null' or 'FINISH'. This is the only way to terminate the loop.
    2.  **Pay attention ot intermediate products**: You must check the output file names generated by other agents in the previous steps, and use these outputs to prepare the parameters for the next step.
    3.  **Accurate task assignment**: For image generation and editing, special attention should be paid to the user's intention. Some image generation tasks require reference, such as images input by the user or images output from previous steps, or when the user mentions "generation based on or reference ***", an agent with reference image generation function is needed instead of purely generation through prompt text.
    4.  **Multi-turn dialogue**: After completing user instructions through several steps, you may continue to receive new user instructions and some results of the previous task may be reused, so pay attention to that situation.
    5. **Image name**: When preparing parameters, the unique identifier for each file is its name, so be careful not to create duplicate file names.

    # Workflow
    1.  **Analyze the current status**:
        - Carefully read the user input task: `{user_prompt}`.
        - If the file that needs to be operated on is provided, you need to review the provided file and check if it has achieved the planning goals of the previous step.
        - If historical summaries and output information are provided, you need to view these historical information
        - Check the current operating status based on the provided files and historical information

    2.  **Decision making and parameter preparation**:
        - If the previous planning step is not completed, consider the reasons for the failure and re-execute it using improved methods.
        - If the previous plan has been completed but the overall goal has not been achieved, continue to generate a plan for the next step
        - Prepare a 'parameters' dict containing all necessary parameters for the experts involved.

    # Expert Agent List:\n\n
    """
