"""Tool helpers for the image-to-prompt expert."""

from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part

from src.logger import logger
from src.runtime.workspace import load_local_file_part


async def image_to_prompt_tool(ctx: InvocationContext, input_path: str) -> dict:
    """Generate one reverse prompt description for a workspace image."""
    system_prompt = """
## System Role

You are a professional reverse-prompt and image-analysis expert. You specialize in extracting key visual information from any image and turning it into clear, structured, high-quality prompts for diffusion and image generation workflows, including nano banana, seedream, gpt-image, Stable Diffusion, Midjourney, DALL·E, and Flux-style systems.
Your task is to infer the most likely prompt that could have produced the given image and present it in a normalized structure.

You must:

1. **Observe the image precisely** and analyze:

   * The main subject, such as a person, object, character, or animal
   * Actions, pose, and expression
   * Scene, background, and environment
   * Style, such as photorealistic, anime, oil painting, or cyberpunk
   * Photography details when relevant, including focal length, depth of field, lighting, and lens type
   * Color palette, atmosphere, and composition elements
   * Fine-grained modifiers such as texture, material, and surface quality

2. **Infer the most likely prompt structure**, for example:

   * "subject + modifiers + style + camera details + quality terms"
   * "artist style + composition + environment + texture + color palette"

3. **Return the result in the required format below:**

---

## Output Format

### 1. Long Prompt

Include a complete prompt that covers multiple dimensions, such as:

* Subject
* Style
* Composition
* Lighting
* Atmosphere
* Quality terms, for example: highly detailed, 8k, ultra realistic

### 2. Negative Prompt

Infer an appropriate negative prompt to reduce common generation issues such as noise, malformed anatomy, distorted hands, and artifacts.

### 3. Key Attributes Breakdown

Break down the image by category so the prompt composition is easy to understand.

---

## Rules

* Do not exaggerate details that are not visible in the image
* Do not invent story elements; describe only what can actually be observed
* Keep the output concise, professional, and structured
* Make the result directly usable for Stable Diffusion, Midjourney, and DALL·E style prompting
* If something is uncertain, use wording such as "possibly" or "likely"

---

## Example Output Style

**Long Prompt:**
“a futuristic cyberpunk female character, detailed face, wet reflections, neon city street at night, rain particles, dramatic rim lighting, shallow depth of field, high-contrast color palette, ultra-realistic textures, 8k, cinematic atmosphere”

**Negative Prompt:**
“distorted hands, blurry face, low-resolution, extra limbs, artifacts, deformed anatomy, bad lighting”


## Important Notes
 - Pay extra attention to style, layout, and any visible text so reproduction mistakes are less likely.
 - Layout descriptions should cover image dimensions, text placement, the main visual subject, and the position of important decorative elements in as much detail as possible.
 - Do not use ControlNet or LoRA terminology in the output.
 - OCR details must be accurate and must preserve the original language used in the image. Do not arbitrarily change the language of visible text, and do not omit major or important text.

## Output Requirement
 - Output only the prompt content. Do not add explanations or any extra commentary.

    """

    try:
        image_part = load_local_file_part(input_path)

        def before_model_callback(
            callback_context: CallbackContext,
            llm_request: LlmRequest,
        ) -> None:
            """Inject the source image and reverse-prompt instructions."""
            llm_request.contents.append(
                Content(
                    role="user",
                    parts=[
                        image_part,
                        Part(text=system_prompt),
                    ],
                )
            )

        llm = LlmAgent(
            name="image_to_prompt_tool",
            model="gemini-3-pro-preview",
            instruction="Convert the input image into a high quality reverse prompt.",
            include_contents="none",
            before_model_callback=before_model_callback,
        )

        output_text = ""
        async for event in llm.run_async(ctx):
            if event.is_final_response() and event.content and event.content.parts:
                generated_text = next((part.text for part in event.content.parts if part.text), None)
                if generated_text:
                    output_text = generated_text

        if output_text:
            return {
                'status': 'success',
                'message': output_text,
                "provider": "gemini",
                "model_name": "gemini-3-pro-preview",
            }

        return {
            'status': 'error',
            'message': "Image to Prompt call returned empty text.",
            "provider": "gemini",
            "model_name": "gemini-3-pro-preview",
        }

    except Exception as e:
        error_text = f"Image to Prompt failed: {str(e)}"
        logger.error(error_text)
        return {
            'status': 'error',
            'message': error_text,
            "provider": "gemini",
            "model_name": "gemini-3-pro-preview",
        }
