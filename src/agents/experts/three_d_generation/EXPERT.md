+++
name = "3DGeneration"
enabled = true
default_provider = "hy3d"
default_model = "3.0"
input_types = ["prompt", "image"]
output_types = ["3d_asset"]
routing_keywords = ["3d", "3D", "model", "asset", "mesh", "stl", "usdz", "fbx", "hunyuan"]
parameter_examples = [
  "{'prompt': 'a wooden toy corgi', 'provider': 'hy3d'(optional), 'model': '3.0|3.1'(optional), 'generate_type': 'normal|lowpoly|sketch|geometry'(optional), 'enable_pbr': false(optional), 'face_count': 10000(optional), 'polygon_type': 'quad'(optional), 'result_format': 'stl|usdz|fbx'(optional), 'timeout_seconds': 900(optional), 'interval_seconds': 8(optional)}",
  "{'input_path': 'workspace/path.png', 'provider': 'hy3d'(optional), 'model': '3.0|3.1'(optional), 'generate_type': 'normal|lowpoly|geometry'(optional), 'enable_pbr': false(optional), 'face_count': 10000(optional), 'polygon_type': 'quad'(optional), 'result_format': 'stl|usdz|fbx'(optional), 'timeout_seconds': 900(optional), 'interval_seconds': 8(optional)}",
  "{'prompt': 'wood carving style', 'input_path': 'workspace/path.png', 'provider': 'hy3d'(optional), 'model': '3.0|3.1'(optional), 'generate_type': 'sketch', 'enable_pbr': false(optional), 'face_count': 10000(optional), 'polygon_type': 'quad'(optional), 'result_format': 'stl|usdz|fbx'(optional), 'timeout_seconds': 900(optional), 'interval_seconds': 8(optional)}",
]
+++

# 3DGeneration

## When to Use

Use this expert to generate 3D asset files from a text prompt, one input image, or prompt-plus-image Sketch mode.

## Routing Notes

- Use prompt-only generation when the user describes a 3D object or asset in text.
- Use image-only generation when the user provides one reference image and wants a 3D asset derived from it.
- Use prompt plus image only with `generate_type=sketch`; current code rejects prompt-plus-image for other generate types.
- Use `result_format` only for supported requested formats: `stl`, `usdz`, or `fbx`.

## Provider Boundaries

- Current implementation only supports provider `hy3d`, Tencent Cloud Hunyuan 3D Pro.
- The default model is `3.0`; the parameters allow `model=3.0` or `model=3.1` when supported by the provider.
- Supported generate types in the current integration are `normal`, `lowpoly`, `sketch`, and `geometry`.
- It supports at most one input image and downloads returned 3D files into the workspace.

## When Not to Use

Do not use this expert for 2D image generation, image editing, video generation, or local file conversion of existing 3D assets.
