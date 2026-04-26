+++
name = "3DGeneration"
enabled = true
default_provider = "hy3d"
default_model = "3.0"
input_types = ["prompt", "image"]
output_types = ["3d_asset"]
routing_keywords = ["3d", "3D", "model", "asset", "mesh", "stl", "usdz", "fbx", "hunyuan", "seed3d", "doubao", "hyper3d", "hitem3d"]
parameter_examples = [
  "{'prompt': 'a wooden toy corgi', 'provider': 'hy3d'(optional), 'model': '3.0|3.1'(optional), 'generate_type': 'normal|lowpoly|sketch|geometry'(optional), 'enable_pbr': false(optional), 'face_count': 10000(optional), 'polygon_type': 'quad'(optional), 'result_format': 'stl|usdz|fbx'(optional), 'timeout_seconds': 900(optional), 'interval_seconds': 8(optional)}",
  "{'input_path': 'workspace/path.png', 'provider': 'hy3d'(optional), 'model': '3.0|3.1'(optional), 'generate_type': 'normal|lowpoly|geometry'(optional), 'enable_pbr': false(optional), 'face_count': 10000(optional), 'polygon_type': 'quad'(optional), 'result_format': 'stl|usdz|fbx'(optional), 'timeout_seconds': 900(optional), 'interval_seconds': 8(optional)}",
  "{'prompt': 'wood carving style', 'input_path': 'workspace/path.png', 'provider': 'hy3d'(optional), 'model': '3.0|3.1'(optional), 'generate_type': 'sketch', 'enable_pbr': false(optional), 'face_count': 10000(optional), 'polygon_type': 'quad'(optional), 'result_format': 'stl|usdz|fbx'(optional), 'timeout_seconds': 900(optional), 'interval_seconds': 8(optional)}",
  "{'input_path': 'workspace/path.png', 'provider': 'seed3d', 'model': 'doubao-seed3d-2-0-260328'(optional), 'file_format': 'glb|obj|usd|usdz'(optional), 'subdivision_level': 'low|medium|high'(optional), 'timeout_seconds': 900(optional), 'interval_seconds': 60(optional)}",
  "{'prompt': 'full-body sci-fi robot, hard-surface design', 'provider': 'hyper3d', 'model': 'hyper3d-gen2-260112'(optional), 'file_format': 'glb|obj|usdz|fbx|stl'(optional), 'mesh_mode': 'Raw|Quad'(optional), 'material': 'PBR|Shaded|All|None'(optional), 'quality_override': 150000(optional), 'hd_texture': true(optional), 'timeout_seconds': 900(optional), 'interval_seconds': 60(optional)}",
  "{'image_urls': ['https://example.com/front.png'], 'provider': 'hitem3d', 'model': 'hitem3d-2-0-251223'(optional), 'file_format': 'obj|glb|stl|fbx|usdz'(optional), 'resolution': '1536|1536pro'(optional), 'face_count': 2000000(optional), 'request_type': 3(optional), 'timeout_seconds': 900(optional), 'interval_seconds': 60(optional)}",
]
+++

# 3DGeneration

## When to Use

Use this expert to generate 3D asset files from a text prompt, one input image, or prompt-plus-image Sketch mode.

## Routing Notes

- Use prompt-only generation when the user describes a 3D object or asset in text.
- Use image-only generation when the user provides one reference image and wants a 3D asset derived from it.
- Use prompt plus image only with `generate_type=sketch`; current code rejects prompt-plus-image for other generate types.
- Prefer provider `seed3d` when the user explicitly asks for Doubao Seed3D or Volcengine image-to-3D.
- Prefer provider `hyper3d` when the user asks for Volcengine Hyper3D, text-to-3D on Ark, or image-to-3D with up to 5 references.
- Prefer provider `hitem3d` when the user asks for Volcengine Hitem3D/Shumei and provides externally accessible image URLs.
- Use `result_format` for `hy3d` supported requested formats: `stl`, `usdz`, or `fbx`.
- Use `file_format` for `seed3d` supported requested formats: `glb`, `obj`, `usd`, or `usdz`.
- Use `file_format` for `hyper3d` supported requested formats: `glb`, `obj`, `usdz`, `fbx`, or `stl`.
- Use `file_format` for `hitem3d` supported requested formats: `obj`, `glb`, `stl`, `fbx`, or `usdz`.

## Provider Boundaries

- Provider `hy3d` remains the default and uses Tencent Cloud Hunyuan 3D Pro.
- `hy3d` default model is `3.0`; the parameters allow `model=3.0` or `model=3.1` when supported by the provider.
- `hy3d` supported generate types are `normal`, `lowpoly`, `sketch`, and `geometry`.
- Provider `seed3d` uses Volcengine Ark model `doubao-seed3d-2-0-260328`.
- `seed3d` is image-to-3D only and requires exactly one `input_path`, `input_paths`, or `image_url`.
- `seed3d` uses `ARK_API_KEY` / `services.ark_api_key` and downloads returned 3D files into the workspace.
- Provider `hyper3d` uses Volcengine Ark model `hyper3d-gen2-260112`.
- `hyper3d` supports English prompt-only text-to-3D and image-to-3D with 1-5 images. Local `input_path` images are sent as data URLs; `image_url`/`image_urls` are passed through directly.
- Provider `hitem3d` uses Volcengine Ark model `hitem3d-2-0-251223`.
- `hitem3d` is image-to-3D only, requires 1-4 externally accessible `image_url`/`image_urls`, and does not accept free-form prompt text.
- All Volcengine providers use `ARK_API_KEY` / `services.ark_api_key` and download returned 3D zip/model files into the workspace.

## When Not to Use

Do not use this expert for 2D image generation, image editing, video generation, or local file conversion of existing 3D assets.
