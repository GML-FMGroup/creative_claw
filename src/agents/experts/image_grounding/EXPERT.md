+++
name = "ImageGroundingAgent"
enabled = true
default_provider = "deepdataspace"
default_model = "DINO-XSeek-1.0"
input_types = ["image", "prompt"]
output_types = ["objects", "bboxes"]
routing_keywords = ["ground", "locate", "find object", "bbox", "bounding box", "object coordinates"]
+++

# ImageGroundingAgent

## When to Use

Use this expert to locate one natural-language target description in one workspace image and return bounding boxes in the original image coordinate space.

## Routing Notes

- Use this expert when the user asks where an object is, asks for object coordinates, or needs bounding boxes for a described visual target.
- Pass exactly one workspace image as `input_path` and a precise object phrase as `prompt`.
- Use spatial, color, attribute, and category details in `prompt` when the image contains multiple similar objects.
- If the next step needs a mask for editing or compositing, use `ImageSegmentationAgent` instead or after grounding.

## Provider Boundaries

- Current integration uses DeepDataSpace DINO-XSeek detection with default model `DINO-XSeek-1.0`.
- The tool requests `targets=["bbox"]` and returns provider `objects` plus simplified `bboxes`.
- It does not save an output image or mask file.
- It requires DDS credentials configured for the DeepDataSpace API.

## When Not to Use

Do not use this expert for general image description, OCR, style analysis, or image generation. Use `ImageUnderstandingAgent` for analysis and `ImageGenerationAgent` or `ImageEditingAgent` for image creation.
