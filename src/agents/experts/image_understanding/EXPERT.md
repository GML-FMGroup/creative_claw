+++
name = "ImageUnderstandingAgent"
enabled = true
default_mode = "description"
input_types = ["image"]
output_types = ["text", "analysis"]
routing_keywords = ["describe image", "analyze image", "ocr", "style analysis", "reverse prompt", "prompt extraction"]
parameter_examples = [
  "{'input_path': 'workspace/path.png', 'mode': 'description|style|ocr|all|prompt'}",
  "{'input_paths': ['workspace/path1.png', ...], 'mode': 'description|style|ocr|all|prompt'}",
  "{'input_paths': ['workspace/path1.png', 'workspace/path2.png'], 'mode': ['description', 'prompt']}",
]
+++

# ImageUnderstandingAgent

## When to Use

Use this expert to analyze one or more workspace images and return text understanding. It is the correct route for descriptions, visual inspection, style analysis, OCR, combined analysis, and reverse-prompt extraction.

## Routing Notes

- Use mode `description` for general image description.
- Use mode `style` for aesthetic, composition, lighting, or art-direction analysis.
- Use mode `ocr` when the user asks to extract readable text from an image.
- Use mode `all` when the user wants description, style, and OCR together.
- Use mode `prompt` when the user wants to recreate an image, reverse engineer a generation prompt, or get reusable prompt language from a reference.
- If the user wants to edit the image after analysis, call `ImageEditingAgent` in a later step with the original workspace path and the derived instructions.

## Provider Boundaries

- This expert returns text and structured understanding results; it does not create image files.
- A single mode can apply to all input images, or a mode list can match the number of input images.
- It includes basic image metadata with successful results when possible.

## When Not to Use

Do not use this expert for image generation, image editing, object segmentation masks, precise bounding boxes, crop/resize/convert operations, or video analysis.
