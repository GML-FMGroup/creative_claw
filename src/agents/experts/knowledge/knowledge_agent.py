import asyncio
import uuid
from typing_extensions import override
from typing import AsyncGenerator, List

from google.adk.agents import LlmAgent
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.tools import ToolContext
from google.adk.agents.callback_context import CallbackContext
from google.adk.sessions import InMemorySessionService
from google.adk.models import LlmRequest
from google.genai.types import Part
from google.genai.types import Content

from conf.system import SYS_CONFIG
from src.logger import logger

async def knowledge_before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest):
    current_parameters = callback_context.state.get('current_parameters', {})
    
    llm_request.contents.append(Content(role='user', parts=[Part(text=f"Current task is: {current_parameters['prompt']}")]))

    if len(current_parameters.get('input_name', []))==0:
        return
    
    # load artifact
    input_name = current_parameters.get('input_name', [])
    artifact_parts = [Part(text="Here are some pictures you can refer to: \n")]
    for i, art_name in enumerate(input_name):
        artifact_parts.append(Part(text=f"image{i+1}, name: {art_name}"))
        art_part = await callback_context.load_artifact(filename=art_name)
        artifact_parts.append(art_part)
    
    llm_request.contents.append(Content(role='user', parts=artifact_parts))

    return



class KnowledgeAgent(BaseAgent):
    model_config = {"arbitrary_types_allowed": True}
    llm: LlmAgent

    def __init__(
        self,
        name: str,
        description: str = '',
        llm_model:str = ''
    ):
        if not llm_model:
            llm_model = SYS_CONFIG.llm_model
        logger.info(f"KnowledgeAgent: using llm: {llm_model}")
        description = 'Analyze input requirement, output refined design scheme or enhanced prompt'

        # The LLM does not automatically receive prior session content.
        llm = LlmAgent(
            name=name,
            model=llm_model,
            description=description,
            instruction=knowledge_intruction,
            before_model_callback=knowledge_before_model_callback
        )
        
        super().__init__(
            name = name,
            description=description,
            llm=llm,
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        current_parameters = ctx.session.state.get('current_parameters', {})
        if 'prompt' not in current_parameters:
            error_text = f"Missing parameters provided to {self.name}, must include: prompt"
            current_output = {"status": "error", "message": error_text}
            logger.error(error_text)

            yield Event(
                author=self.name,
                content=Content(role='model', parts=[Part(text=error_text)]),
                actions=EventActions(state_delta={"current_output":current_output})
            )
            return
        
        text_list = []
        async for event in self.llm.run_async(ctx):
            if event.is_final_response() and event.content and event.content.parts:
                generated_text = next((part.text for part in event.content.parts if part.text), None)
                if not generated_text:
                    continue
                yield event # model response will be appended to session
                text_list.append(generated_text)

        if len(text_list)==0:
            message = f"{self.name} generate response failed"
            logger.error(message)
            current_output = {'status': 'error', 'message': message}
        else:
            message = f"{self.name} has completed the design."
            output_text = '\n'.join(text_list)
            current_output = {'status': 'success', 'message': message, 'output_text': output_text}
        
        yield Event(
            author='KnowledgeAgent',
            content=Content(role='model', parts=[Part(text=message)]),          
            actions=EventActions(state_delta={'current_output':current_output})
        )



knowledge_intruction = """
You are a professional visual designer who will accept an image generation task, sometimes with several reference images.
Your task is to output image design schemes based on requirements and references, and then generate prompts based on the schemes to guide subsequent image generation.

# task input
 - Design requirement: user's description of image, or a design request of a product or image.
 - Reference images: images of varying quantities used for reference

# task output
 - Design scheme: Detailed image design proposal that includes specific settings for the foreground and background of the image, as well as specific designs for visual elements that may exist in the image
 - Image generation prompt: Prompt based on the design used to guide subsequent image generation

# Task steps (strictly follow)
Firstly, you need to understand the user's image generation or design task and the number of designs and prompts that need to be output, and then output them in the following two steps:
Step 1: Output a specific design proposal in markdown format, including detailed explanations of the overall and local designs
Step 2: Refer to the design scheme generated in the previous step and generate one or more image prompts

# Design scheme requirements
Your visual design proposal must include the following elements:
1. Foreground visual elements
 - If the task involves designing a certain product, based on existing knowledge and provided information, use your imagination and creativity to generate a detailed design plan for the product
 - Detailed description of all foreground elements, including style, color, texture, etc
 - Detailed design descriptions of other necessary special elements that may be included in the task, such as fonts, symbols, logos, etc.

2. Screen layout
 - Design the overall layout of the image, including detailed layouts of all foreground and background elements
 - The layout of possible special elements, such as logos, fonts, special symbols, or lighting.
 - Layout can add personalized design to meet design goals, especially for product display images, where a real-life scenario can be designed to highlight the product.

3. Macro atmosphere
 - Detailed overall visual atmosphere design, including color tones, environment, and implicit information and emotions that the image needs to convey, such as furniture conveying a warm atmosphere, while art posters conveying avant-garde design.
 - Various professional photography parameters, such as exposure, saturation, contrast
 - Other artistic styles that can enhance visual expression.

# Image generation prompt requirements
After generating the design scheme, you must generate one or more prompts based on the scheme. You must obey the following rules:
1. Strictly comply with design requirements
 - The prompt must include all the contents of the design proposal and strictly consistent with the design.
 - Do not add elements that are not included in the design or modify the the design in the prompt

2. Compact and refined language
 - Prompt needs to remove redundant information while including all design scheme information
 - Prompt is plain text and special formats such as md or json are prohibited.

3. Output one or more prompts
 - Sometimes users require multiple prompts. When outputting, you must use formats such as prompt1: ***, prompt2: *** .

# Special attention
1. In the two-step execution process, you need to indicate at the beginning of the output of the first and second steps respectively: 'This is/are the design scheme(s):', 'This is/are the image generation prompt(s)'
2. Sometimes users may request to output multiple different designs and prompts for the same goal, and in this case, you need to specify the number of generated designs and prompts and output the request numbers of design and prompt.
3. If the user requests to output multiple images, it is prohibited to generate multiple images by generating a grid like image. Instead, multiple different prompts must be output.
4. If a user requests to design a certain product, what you should actually design is the display image of the product. You can use your imagination appropriately. For example, you can design the following scene for a vacuum cleaner product promotional image: a family happily using a vacuum cleaner to clean the house.
5. For product or poster design proposals, you must carefully consider whether to add text and symbols to enhance the artistic effect.

"""
