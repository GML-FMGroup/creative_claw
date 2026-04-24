+++
name = "ImageBasicOperations"
enabled = true
input_types = ["image"]
output_types = ["image", "metadata"]
routing_keywords = ["crop", "resize", "rotate", "flip", "convert image", "image info", "metadata"]
+++

# ImageBasicOperations

## When to Use

Use this expert for deterministic local image operations inside the workspace: `crop`, `rotate`, `flip`, `info`, `resize`, and `convert`.

## Routing Notes

- Use `info` before model calls when the user or provider constraints require image dimensions, format, mode, or metadata.
- Use `resize`, `crop`, `rotate`, `flip`, or `convert` when the request is purely file manipulation and does not require AI generation.
- Use this expert to preprocess images before provider calls, for example when video or image providers have input size or format limits.
- Pass one `operation` and one `input_path` per call.

## Provider Boundaries

- This is a deterministic local operation wrapper around built-in workspace image tools.
- `info` returns structured metadata and does not create a new image file.
- File-producing operations save a new workspace file and leave the original input unchanged.
- It does not understand image content semantically and does not create or edit pixels using a generative model.

## When Not to Use

Do not use this expert for prompt-based image generation, reference-image editing, OCR, style analysis, grounding, or segmentation. Use the corresponding model-based image experts for those tasks.
