+++
name = "ImageEditingAgent"
enabled = true
default_provider = "nano_banana"
input_types = ["image", "prompt"]
output_types = ["image"]
routing_keywords = ["image edit", "modify image", "reference image", "inpaint", "replace", "change background"]
+++

# ImageEditingAgent

## When to Use

Use this expert to modify one or more existing workspace images with an editing prompt. It is the correct route when the user provides an image and asks to change, replace, restyle, extend, combine, or use it as a reference.

## Routing Notes

- Always pass `input_path` or `input_paths` with workspace-relative image paths.
- Use `nano_banana` by default for image editing.
- Use `seedream` only when requested or clearly needed.
- If the user needs a cutout, mask, localized edit, or region-specific preparation, call `ImageSegmentationAgent` first and then reuse the returned `mask_path`.
- If the user only asks for a brand-new image from text with no reference image, use `ImageGenerationAgent` instead.
- If the user asks to understand the image before editing, use `ImageUnderstandingAgent` first.

## Provider Boundaries

- `nano_banana` and `seedream` can consume one or more input images in the current integration.
- The prompt should explain the role and order of multiple input images.
- This expert returns new edited image files and does not overwrite the original input image.

## When Not to Use

Do not use this expert for text-only image generation, OCR, style analysis, reverse-prompt extraction, object grounding, segmentation mask creation, or deterministic crop/resize/convert operations.
