+++
name = "ImageBasicOperations"
enabled = true
input_types = ["image"]
output_types = ["image", "metadata"]
routing_keywords = ["crop", "resize", "rotate", "flip", "convert image", "image info", "metadata"]
parameter_examples = [
  "{'operation': 'crop', 'input_path': 'workspace/path.png', 'left': 10, 'top': 20, 'right': 200, 'bottom': 180}",
  "{'operation': 'rotate', 'input_path': 'workspace/path.png', 'degrees': 90, 'expand': true}",
  "{'operation': 'flip', 'input_path': 'workspace/path.png', 'direction': 'horizontal|vertical'}",
  "{'operation': 'info', 'input_path': 'workspace/path.png'}",
  "{'operation': 'resize', 'input_path': 'workspace/path.png', 'width': 1024, 'height': 1024, 'keep_aspect_ratio': true, 'resample': 'nearest|bilinear|bicubic|lanczos'(optional)}",
  "{'operation': 'convert', 'input_path': 'workspace/path.png', 'output_format': 'png|jpg|jpeg|webp', 'mode': 'RGB'(optional), 'quality': 90(optional)}",
]
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
