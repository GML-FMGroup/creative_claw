+++
name = "ImageSegmentationAgent"
enabled = true
default_provider = "deepdataspace"
default_model = "DINO-X-1.0"
input_types = ["image", "prompt"]
output_types = ["mask", "objects", "bboxes"]
routing_keywords = ["segment", "mask", "cut out", "isolate", "remove background", "region edit"]
parameter_examples = [
  "{'input_path': 'workspace/path.png', 'prompt': 'object description', 'model': 'DINO-X-1.0'(optional), 'threshold': 0.25(optional)}",
]
+++

# ImageSegmentationAgent

## When to Use

Use this expert to segment one natural-language target in one workspace image and save a binary mask image file for downstream image editing or compositing.

## Routing Notes

- Use this expert before `ImageEditingAgent` when the requested edit needs a localized region, mask, cutout, subject isolation, or precise object replacement.
- Pass exactly one workspace image as `input_path` and one target phrase as `prompt`.
- Use `threshold` only when the user needs stricter or looser detection; otherwise keep the default `0.25`.
- Reuse the returned `mask_path` in later image-editing or file-processing steps.

## Provider Boundaries

- Current integration uses DeepDataSpace DINO-X detection with default model `DINO-X-1.0`.
- The tool requests both `bbox` and `mask` targets, receives COCO RLE masks, merges them, and saves one binary PNG mask.
- Output includes provider `objects`, simplified `bboxes`, `threshold`, and `mask_path`.
- It requires DDS credentials configured for the DeepDataSpace API.

## When Not to Use

Do not use this expert for bounding boxes only; use `ImageGroundingAgent` for that. Do not use it for full image description, OCR, or new image generation.
