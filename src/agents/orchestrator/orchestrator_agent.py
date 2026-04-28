"""Planning-oriented orchestrator runtime for Creative Claw."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.run_config import RunConfig
from google.adk.apps import App
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part

from conf.agent import EXPERTS_LIST
from conf.llm import build_llm, resolve_llm_model_name
from conf.system import SYS_CONFIG
from src.agents.experts.video_generation.capabilities import build_video_generation_routing_notes
from src.agents.orchestrator.final_response import (
    ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
    OrchestratorFinalResponse,
)
from src.logger import logger
from src.runtime.step_events import (
    CreativeClawStepEventPlugin,
    publish_orchestration_step_event,
    step_event_streaming_active,
)
from src.runtime.expert_dispatcher import dispatch_expert_call
from src.runtime.expert_registry import build_expert_contract_summary
from src.runtime.tool_display import format_tool_args, stringify_value, summarize_tool_result
from src.runtime.workspace import (
    build_workspace_file_record,
    load_local_file_part,
    looks_like_image,
    relocate_generated_output,
    resolve_workspace_path,
    workspace_relative_path,
)
from src.production.design.tool import run_design_production as run_design_production_tool
from src.production.ppt.tool import run_ppt_production as run_ppt_production_tool
from src.production.short_video.tool import run_short_video_production as run_short_video_production_tool
from src.skills import get_skill_registry
from src.tools.builtin_tools import (
    BuiltinToolbox,
)

_PLUGIN_MANAGED_TOOL_NAMES = {
    "list_dir",
    "glob",
    "grep",
    "read_file",
    "write_file",
    "edit_file",
    "image_crop",
    "image_rotate",
    "image_flip",
    "image_info",
    "image_resize",
    "image_convert",
    "video_info",
    "video_extract_frame",
    "video_trim",
    "video_concat",
    "video_convert",
    "audio_info",
    "audio_trim",
    "audio_concat",
    "audio_convert",
    "exec_command",
    "process_session",
    "web_search",
    "web_fetch",
    "run_design_production",
    "run_ppt_production",
    "run_short_video_production",
}

_AUTO_OUTPUT_TOOL_NAMES = {
    "image_crop",
    "image_rotate",
    "image_flip",
    "image_resize",
    "image_convert",
    "video_extract_frame",
    "video_trim",
    "video_concat",
    "video_convert",
    "audio_trim",
    "audio_concat",
    "audio_convert",
}

_DISPLAY_TOOL_TITLES = {
    "list_skills": "List Skills",
    "read_skill": "Read Skill",
    "list_session_files": "List Session Files",
    "run_design_production": "Design Production",
    "run_ppt_production": "PPT Production",
    "run_short_video_production": "Short Video Production",
}


def _format_file_summary(file_info: dict[str, Any], *, index: int) -> str:
    """Render one compact workspace file summary."""
    file_name = str(file_info.get("name", "")).strip() or f"file_{index}"
    file_path = str(file_info.get("path", "")).strip()
    file_description = str(file_info.get("description", "")).strip()
    metadata_parts = [f"name={file_name}", f"path={file_path}"]
    if file_description:
        metadata_parts.append(f"description={file_description}")
    turn = file_info.get("turn")
    step = file_info.get("step")
    expert_step = file_info.get("expert_step")
    if turn is not None:
        metadata_parts.append(f"turn={turn}")
    if step is not None:
        metadata_parts.append(f"step={step}")
    if expert_step is not None:
        metadata_parts.append(f"expert_step={expert_step}")
    return f"file {index}: {'; '.join(metadata_parts)}"


def _format_turn_file_history(label: str, history: list[dict[str, Any]]) -> list[str]:
    """Render one turn-grouped file history section."""
    if not history:
        return []
    rendered = [label]
    for entry in history:
        turn = int(entry.get("turn", 0) or 0)
        files = list(entry.get("files") or [])
        if not files:
            rendered.append(f"- Turn {turn}: no files")
            continue
        rendered.append(
            f"- Turn {turn}: {' | '.join(_format_file_summary(file_info, index=index) for index, file_info in enumerate(files, start=1))}"
        )
    return rendered


def _latest_generated_files(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the latest generated file batch from current-turn or historical state."""
    generated = list(state.get("generated") or [])
    if generated:
        return generated
    generated_history = list(state.get("generated_history") or [])
    for entry in reversed(generated_history):
        files = list(entry.get("files") or [])
        if files:
            return files
    files_history = list(state.get("files_history") or state.get("artifacts_history") or [])
    return _select_latest_non_channel_files(files_history)


async def orchestrator_before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Inject compact runtime state and recent workspace files into the model request."""
    state = callback_context.state
    turn_index = state.get("turn_index", 0)
    step = state.get("step", 0)
    expert_step = state.get("expert_step", 0)
    workflow_status = state.get("workflow_status", "running")
    delivery_channel = str(state.get("channel", "")).strip()
    delivery_chat_id = str(state.get("chat_id", "")).strip()
    delivery_sender_id = str(state.get("sender_id", "")).strip()
    uploaded = list(state.get("uploaded") or state.get("input_files") or state.get("input_artifacts") or [])
    uploaded_history = list(state.get("uploaded_history") or [])
    generated = list(state.get("generated") or [])
    generated_history = list(state.get("generated_history") or [])

    summary_lines = [
        f"# Workflow status: {workflow_status}",
        f"# Current turn: {turn_index}",
        f"# Executed actions: {step}",
        f"# Expert calls: {expert_step}",
        f"# User task:\n{state.get('user_prompt', '')}",
    ]

    if delivery_channel:
        delivery_parts = [f"channel={delivery_channel}"]
        if delivery_chat_id:
            delivery_parts.append(f"chat_id={delivery_chat_id}")
        if delivery_sender_id:
            delivery_parts.append(f"sender_id={delivery_sender_id}")
        summary_lines.append(f"# Delivery context: {'; '.join(delivery_parts)}")

    if uploaded:
        summary_lines.append("# Uploaded files in current turn:")
        summary_lines.extend(
            f"- {_format_file_summary(file_info, index=index)}"
            for index, file_info in enumerate(uploaded, start=1)
        )

    summary_lines.extend(_format_turn_file_history("# Uploaded file history by turn:", uploaded_history))

    if generated:
        summary_lines.append("# Generated files in current turn:")
        summary_lines.extend(
            f"- {_format_file_summary(file_info, index=index)}"
            for index, file_info in enumerate(generated, start=1)
        )

    summary_lines.extend(_format_turn_file_history("# Generated file history by turn:", generated_history))

    summary_history = state.get("summary_history", [])
    message_history = state.get("message_history", [])
    if summary_history and message_history:
        summary_lines.append("# Execution history:")
        for index, (summary, message) in enumerate(zip(summary_history, message_history), start=1):
            summary_lines.append(f"- Step {index}: target={summary}; result={message}")

    latest_file_group = _latest_generated_files(state)
    if latest_file_group:
        latest_paths = ", ".join(
            str(file_info.get("path", "")).strip()
            for file_info in latest_file_group
            if str(file_info.get("path", "")).strip()
        )
        if latest_paths:
            summary_lines.append(f"# Most recent available output files: {latest_paths}")

    summary_lines.extend(
        [
            "# Final response contract:",
            "- When the task is complete, return the final structured response with `reply_text` and `final_file_paths`.",
            "- `reply_text` must contain the complete user-facing reply in the user's language.",
            "- `final_file_paths` must contain exact workspace-relative paths from the current session, or be `[]` when no attachments are needed.",
            "- Use `list_session_files(section=\"latest_output\")` when you need the exact attachment paths to return.",
        ]
    )

    llm_request.contents.append(
        Content(role="user", parts=[Part(text="\n".join(summary_lines))])
    )

    reference_groups = [
        ("Uploaded workspace files from current turn:", uploaded),
        ("Generated workspace files from current turn:", generated),
    ]
    if not any(file_group for _, file_group in reference_groups):
        return

    file_parts: list[Part] = []
    for label, file_group in reference_groups:
        if not file_group:
            continue
        file_parts.append(Part(text=f"{label}\n"))
        for index, file_info in enumerate(file_group, start=1):
            file_parts.append(
                Part(
                    text=(
                        f"File {index}: {file_info['name']}. "
                        f"Path: {file_info.get('path', '')}. "
                        f"Description: {file_info.get('description', '')}\n"
                    )
                )
            )
            file_path = str(file_info.get("path", "")).strip()
            if file_path and looks_like_image(file_path):
                file_parts.append(load_local_file_part(file_path))

    llm_request.contents.append(Content(role="user", parts=file_parts))


def _select_latest_non_channel_files(files_history: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return the latest non-channel file batch recorded in session state."""
    for file_group in reversed(files_history):
        if file_group and any(str(file_info.get("source", "")).strip() != "channel" for file_info in file_group):
            return file_group
    return []


def _collect_known_workspace_paths(state: dict[str, Any]) -> set[str]:
    """Collect every normalized workspace path already tracked in the current session."""
    known_paths: set[str] = set()

    def _record(file_group: list[dict[str, Any]]) -> None:
        for file_info in file_group:
            path = str(file_info.get("path", "")).strip()
            if path:
                known_paths.add(path)

    _record(list(state.get("uploaded") or state.get("input_files") or []))
    _record(list(state.get("generated") or []))
    _record(list(state.get("new_files") or []))
    for entry in list(state.get("uploaded_history") or []):
        if isinstance(entry, dict):
            _record(list(entry.get("files") or []))
    for entry in list(state.get("generated_history") or []):
        if isinstance(entry, dict):
            _record(list(entry.get("files") or []))
    for file_group in list(state.get("files_history") or []):
        if isinstance(file_group, list):
            _record(list(file_group))
    return known_paths


def _normalize_final_response_paths(paths: list[str], *, state: dict[str, Any]) -> list[str]:
    """Validate final attachment paths against the current session file history."""
    known_paths = _collect_known_workspace_paths(state)
    normalized_paths: list[str] = []
    seen_paths: set[str] = set()
    for raw_path in paths:
        if not isinstance(raw_path, str):
            raise ValueError("Each final attachment path must be a string.")
        cleaned_path = raw_path.strip()
        if not cleaned_path:
            continue
        if Path(cleaned_path).is_absolute():
            raise ValueError("Final attachment paths must be workspace-relative, not absolute.")
        relative_path = workspace_relative_path(cleaned_path)
        if relative_path not in known_paths:
            raise ValueError(
                f"Final attachment path '{relative_path}' is not part of the current session file history."
            )
        resolved = resolve_workspace_path(relative_path)
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"Final attachment path '{relative_path}' does not resolve to an existing file.")
        if relative_path in seen_paths:
            continue
        seen_paths.add(relative_path)
        normalized_paths.append(relative_path)
    return normalized_paths


class Orchestrator:
    """Plan one workflow step at a time with skills and builtin tools."""

    def __init__(
        self,
        session_service: InMemorySessionService,
        artifact_service: InMemoryArtifactService,
        expert_agents: dict[str, BaseAgent],
        app_name: str = SYS_CONFIG.app_name,
        save_dir: str = "",
        llm_model: str = "",
    ) -> None:
        self.app_name = app_name
        self.session_service = session_service
        self.artifact_service = artifact_service
        self.expert_agents = expert_agents
        self.save_dir = save_dir
        self.uid = ""
        self.sid = ""
        self.skill_registry = get_skill_registry()
        self.toolbox = BuiltinToolbox()

        model_name = resolve_llm_model_name(llm_model or SYS_CONFIG.llm_model)
        logger.info("OrchestratorAgent: using llm: {}", model_name)

        self.agent = LlmAgent(
            name="CreativeClawOrchestrator",
            model=build_llm(llm_model or SYS_CONFIG.llm_model),
            instruction=self._build_instruction(),
            before_model_callback=orchestrator_before_model_callback,
            output_schema=OrchestratorFinalResponse,
            output_key=ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
            tools=[
                self.list_skills,
                self.read_skill,
                self.list_dir,
                self.glob,
                self.grep,
                self.read_file,
                self.write_file,
                self.edit_file,
                self.image_crop,
                self.image_rotate,
                self.image_flip,
                self.image_info,
                self.image_resize,
                self.image_convert,
                self.video_info,
                self.video_extract_frame,
                self.video_trim,
                self.video_concat,
                self.video_convert,
                self.audio_info,
                self.audio_trim,
                self.audio_concat,
                self.audio_convert,
                self.exec_command,
                self.process_session,
                self.web_search,
                self.web_fetch,
                self.list_session_files,
                self.run_design_production,
                self.run_ppt_production,
                self.run_short_video_production,
                self.invoke_agent,
            ],
        )
        self.app = App(
            name=self.app_name,
            root_agent=self.agent,
            plugins=[CreativeClawStepEventPlugin()],
        )
        self.runner = Runner(
            app=self.app,
            app_name=self.app_name,
            session_service=self.session_service,
            artifact_service=self.artifact_service,
        )

    def _build_instruction(self) -> str:
        """Build the planner instruction for the orchestrator."""
        available_experts = "\n".join(
            str(expert) for expert in EXPERTS_LIST if expert.enable
        )
        skills_summary = self.skill_registry.build_summary()
        expert_contracts = build_expert_contract_summary()
        video_generation_routing_notes = build_video_generation_routing_notes()

        return f"""
You are Creative Claw's primary user-facing orchestrator.

Your job is to inspect the current state, use skills and tools when helpful, and directly complete the user's request in this invocation whenever possible.
Do not create a full upfront plan unless the user explicitly asks for one.
You can use built-in tools, skills, production tools, `invoke_agent`, and your own reasoning to complete the task.
You are the main agent, and expert agents are supporting capabilities invoked through `invoke_agent`.
Prefer completing the task directly instead of describing an internal workflow.

You can use five kinds of capabilities:
1. Skills from local markdown files
2. Built-in local file tools inside the fixed workspace
3. Built-in shell and web tools
4. Existing expert agents through `invoke_agent(agent_name, prompt)`
5. Typed production tools for multi-step production capabilities

Rules:
- Treat yourself as the main conversational agent. Reply to the user's actual request, not to an internal workflow.
- When a skill seems relevant, call `list_skills` first and then `read_skill`.
- Never invent skill content. Read the actual `SKILL.md` before using it deeply.
- Prefer direct execution over abstract planning.
- Use built-in tools for local workspace work: `list_dir`, `glob`, `grep`, `read_file`, `write_file`, `edit_file`, `image_crop`, `image_rotate`, `image_flip`, `image_info`, `image_resize`, `image_convert`, `video_info`, `video_extract_frame`, `video_trim`, `video_concat`, `video_convert`, `audio_info`, `audio_trim`, `audio_concat`, `audio_convert`, `exec_command`, `process_session`, `web_search`, `web_fetch`.
- Use `list_session_files(section=...)` when you need the exact normalized workspace paths already tracked in the current session state.
- For HTML-centered design work such as UI mockups, dashboards, landing pages, product detail pages, one-pagers, wireframes, or lightweight interactive HTML prototypes, use `run_design_production` instead of manually chaining file writes, image generation, screenshots, and validation in the orchestrator.
- Current Design production supports a P0a placeholder HTML pipeline through `run_design_production(action="start", placeholder_design=true)`. Use this mode when validating the production framework. For non-placeholder Design production, `action="start"` currently prepares a design direction review before HTML generation.
- Design production's core artifact is HTML. Route pure image/poster/cover generation away from Design unless the image is only an embedded asset for an HTML design. Route editable PPTX generation away from Design and to the PPT product line when that exists.
- Use `run_design_production(action="view", view_type=...)` when the user asks to inspect the Design brief, design system, extracted design-system usage, component inventory, accessibility report, layout, preview, diagnostics, lineage, page handoff, quality report, events, or artifacts. Supported view types are `overview`, `brief`, `design_system`, `design_system_extraction`, `components`, `accessibility`, `layout`, `preview`, `diagnostics`, `lineage`, `pages`, `quality`, `events`, and `artifacts`.
- Use `run_design_production(action="analyze_revision_impact", user_response=...)` before applying targeted design revisions when the user asks what will change. Use `apply_revision` only after the user confirms the revision.
- When `run_design_production` returns completed artifacts, include those artifact paths in `final_file_paths`.
- For PPT, slides, PowerPoint, deck, presentation, 演示文稿, 汇报, or converting a brief/source/template into slides, use `run_ppt_production` instead of manually chaining text/image/file tools. PPT production is for durable, reviewable `.pptx` outputs.
- Current PPT production supports native editable PPTX generation, TXT/MD/DOCX plus lightweight text-layer PDF source-document summaries, PPTX template summaries, optional brief review, outline review, deck spec review, generated slide previews, per-slide PPTX segments, stale slide preview/segment regeneration, a render manifest, a quality report with source fact coverage checks, status/view actions, and revision impact analysis. Template editing, PDF OCR/deep layout extraction, and patching regenerated pages back into an existing final PPTX remain future capabilities.
- Start PPT production with `run_ppt_production(action="start", user_prompt=..., input_files=..., render_settings=...)`. The standard gated reviews are `outline_review`, then `deck_spec_review`, then `final_preview_review`; if the user asks to confirm deck direction before outline planning, pass `render_settings.brief_review=true` to insert `brief_review` before `outline_review`; targeted stale-page regeneration may insert `page_preview_review` before returning to `deck_spec_review`. Call `resume` with `decision="approve"` only after the user approves the current review payload. Approving the final preview completes production and projects final artifacts into session state.
- Use `run_ppt_production(action="view", view_type=...)` when the user asks for PPT status, inputs, document summary, template summary, outline, deck spec, previews, manifest, quality, events, or artifacts. Use `view_type="manifest"` for delivery paths across final PPTX, previews, per-slide segments, and quality files; use `view_type="quality"` for QC, validation, or delivery readiness.
- Use `run_ppt_production(action="add_inputs", input_files=...)` when the user adds a PPT template, source document, or visual reference to the current PPT production. This keeps the same production session and returns to outline review.
- Use `run_ppt_production(action="analyze_revision_impact", user_response=...)` before applying targeted PPT changes when the user asks what would be affected. If the user names a slide/page number, include `slide_number`; if a review payload item id is known, include `target_kind` (`outline_entry` or `deck_slide`) and `target_id`. Use `apply_revision` only after the user confirms the change; targeted outline changes return to `outline_review`, while targeted deck-slide changes return to `deck_spec_review`. If a targeted deck-slide revision leaves stale previews and the user wants to refresh those pages before approving the full deck rebuild, call `run_ppt_production(action="regenerate_stale_segments")`; this refreshes stale preview PNGs and per-slide PPTX segments only, returns `page_preview_review`, and keeps the final PPTX and quality report stale until the regenerated pages and deck spec are approved.
- When `run_ppt_production` returns completed artifacts, include those artifact paths in `final_file_paths`.
- For short-video production workflows that need durable state, review/resume, generated artifacts, or future iteration, use `run_short_video_production` instead of manually chaining video experts in the orchestrator.
- Current short-video production supports P0a placeholder rendering and P1e gated shot-segment review with partial segment regeneration, explicit provider selection, and final quality reporting for `product_ad`, `cartoon_short_drama`, and `social_media_short`. Use `action="start"` with `placeholder_assets=true` when validating the production framework. For real short-video production, use `action="start"` with `placeholder_assets=false`; every gated review returns `needs_user_review`, the first review is `storyboard_review`, the second gated review is `asset_plan_review`, and approving the asset plan generates one provider shot segment before returning `shot_review`. Continue with `action="resume"` only after each user approval; a `shot_review` approval either generates the next segment or finalizes completed artifacts with a quality report.
- When starting short-video production from uploaded reference files, pass the uploaded workspace file records or workspace-relative path strings in `input_files`; do not describe the upload only in `user_prompt`.
- Short-video production uses Seedance 2.0 native audio by default. When the user asks for faster generation, pass `render_settings.provider="seedance"` and `render_settings.model_name="doubao-seedance-2-0-fast-260128"`; keep `resolution` at `720p` because Seedance 2.0 fast does not support 1080p.
- When the user explicitly asks for Veo, Veo+TTS, or separate TTS voiceover in short-video production, pass `render_settings.provider="veo_tts"`; this is a compatibility runtime, not a fallback. Do not switch providers after a failure unless the user asks to switch.
- When presenting a short-video production review, only offer aspect ratios returned by the production tool; Seedance currently offers `9:16`, `16:9`, and `1:1`, while Veo+TTS currently offers `9:16` and `16:9`; do not recommend unsupported ratios as selectable options.
- Use `run_short_video_production(action="view", view_type=...)` when the user asks where production is, what the brief/storyboard/asset plan/timeline/quality report/events/artifacts are, or what generated files belong to the current production session. Use `view_type="quality"` when the user asks for quality, QC, validation, checks, issues, or whether the current/final video meets requirements.
- Use `run_short_video_production(action="add_reference_assets", input_files=...)` when the user adds or replaces product/person/style reference images for the current short-video production. `input_files` may be workspace file records or workspace-relative path strings from uploaded session files. This keeps the same production session and returns to storyboard review so reference usage can be confirmed.
- Use `run_short_video_production(action="analyze_revision_impact", user_response=...)` with `targets` and `notes` when the user asks to change a specific shot, generated shot segment, voiceover, asset plan item, timeline, or final output and wants to know what will be affected before applying the change.
- Use `run_short_video_production(action="apply_revision", user_response=...)` only after the user confirms they want to apply a short-video revision. This mutates production state, marks affected outputs stale, and returns to review; targeted `shot_asset_plan` or `shot_artifact` revisions should stale only the impacted segment when possible. Do not call `resume` for regeneration until the user approves the updated plan.
- Do not manually call video/TTS experts for short-video production before the production tool has returned an approved review state.
- When `run_short_video_production` returns completed artifacts, include those artifact paths in `final_file_paths`.
- All file paths must be relative to the fixed `workspace` directory unless the tool explicitly returns a workspace-relative path.
- Inspect local files with `list_dir`, `glob`, `grep`, and `read_file` before changing them when the path or contents are uncertain.
- Use local image, video, and audio tools for lightweight deterministic preprocessing, and keep the returned suffixed output path instead of overwriting the original by default.
- Keep changes small and reviewable, and re-check the latest state after each meaningful action.
- For coding, debugging, and file-editing tasks, prefer solving the task directly with built-in tools before delegating to an expert.
- For coding tasks, you may inspect files, write or edit code, run targeted commands with `exec_command`, inspect stdout and stderr, and iterate based on the results.
- Use `glob` to locate candidate files quickly and `grep` to find symbols, messages, and code snippets before reading full files.
- For long-running commands, start them with `exec_command(background=true, yield_ms=...)` and then use `process_session` to list, poll, inspect logs, write stdin, kill, or remove sessions.
- After writing or editing code, prefer running a small verification command with `exec_command` before finishing when verification is feasible.
- For ordinary conversation, explanations, brainstorming, lightweight analysis, and tasks that built-in tools can complete, finish directly instead of delegating.
- When planning expert parameters, pass workspace file paths with `input_path` or `input_paths` instead of artifact names.
- `input_name` is legacy and should not be used unless compatibility fallback is absolutely required.
- When using `ImageGenerationAgent`, you may pass optional `provider`, `aspect_ratio`, and `resolution`.
- When using `ImageEditingAgent`, you may pass optional `provider`.
- When using `VideoGenerationAgent`, you may pass optional `prompt_rewrite`, `provider`, `mode`, `aspect_ratio`, `resolution`, `duration_seconds`, `generate_audio`, `watermark`, `negative_prompt`, `person_generation`, `seed`, `model_name`, and `kling_mode`.
- For exact dialogue or native generated audio with Seedance 2.0, set `provider="seedance"`, `generate_audio=true`, and `prompt_rewrite="off"` so quoted dialogue is preserved.
{video_generation_routing_notes}
- For provider `veo`, mode `video_extension` accepts one workspace video via `input_path` or `input_paths`.
- For provider `kling`, use only `prompt`, `first_frame`, `first_frame_and_last_frame`, or `multi_reference`. `multi_reference` expects 2-4 workspace images through `input_paths`. If Kling input images do not meet the documented limits, inspect them with `image_info` and decide whether to preprocess them with `image_resize` or other local image tools first. The Kling expert does not auto-resize or auto-crop inputs. Do not route Kling calls to `reference_asset`, `reference_style`, or `video_extension`.
- For cutout, local edit, inpaint-style masking, or region-targeted image workflows, prefer calling `ImageSegmentationAgent` first, then read `current_output.results[0].mask_path` from the expert result and reuse that workspace path in the next step.
- Default image provider is `nano_banana` unless the user or task clearly requires `seedream`.
- When the user refers to a previously generated image or file without re-uploading it, inspect the workspace file history and use the most recent relevant workspace path.
- Prefer files already listed in the current session file history. Do not inspect or reuse files from unrelated session directories unless the user explicitly asks for cross-session access.
- Use `list_session_files(section="latest_output")` or the current session file history whenever you need the exact workspace-relative paths for the final response attachments.
- Do not invent attachment paths. Only return exact workspace-relative paths already known in the current session state.
- Only choose an expert agent when the task needs specialized image, search, or other expert capability that built-in tools and direct reasoning cannot handle well.
- When calling `invoke_agent`, pass a complete expert brief.
- For experts that need several parameters, encode the `prompt` argument as a JSON object string that contains the exact expert parameters.
- Prefer workspace paths in that JSON object, such as `input_path` or `input_paths`.
- `invoke_agent` returns structured data including status, message, optional output_text, and output_files.
- Keep the language of any user-facing summary or reply aligned with the user's language.
- If the user primarily writes in Chinese, reply in Chinese. If the user primarily writes in English, reply in English.
- If the user mixes languages, follow the primary language of the user's latest message.
- Use the current delivery channel context when it helps adapt formatting or tone for the reply.
- Do not expose raw routing identifiers such as `chat_id` or `sender_id` unless the user explicitly asks for them.

Creative workflow routing hints:
- If the user has a topic, campaign brief, or rough idea but does not yet have scenes, hook, or storyboard structure, prefer reading `creative-brief-to-storyboard` before jumping into generation.
- If the user already has narration, script, or storyboard text and now needs image prompts or video prompts, prefer reading `narration-to-visual-prompts`.
- If the user already has photos or video clips and wants the story built around those assets, prefer reading `asset-to-script`.
- If the user mainly wants to translate style direction, mood, or art direction into reusable prompt language, prefer reading `style-brief-to-prompt`.
- If the request mixes idea, script, assets, style, generation, and review in a way that is not immediately clear, prefer reading `creative-workflow-router` first to choose the smallest correct path.
- If the user asks whether a storyboard, prompt pack, or generated result is ready, consistent, or worth revising before spending more generation budget, prefer reading `creative-qc`.
- For these creative routing cases, do not skip straight to `ImageGenerationAgent` or `VideoGenerationAgent` when the user still needs planning, prompt derivation, or QC.
- After reading a relevant creative skill, follow its handoff guidance and pass exact expert parameters as a JSON object string to `invoke_agent`.
- If no skill is needed because the user gave a clear final generation request, execute directly with the smallest suitable expert call.

Response Requirements:
- Put the complete user-facing natural-language reply into `reply_text` in the structured final response.
- Put any final attachments into `final_file_paths` as exact workspace-relative paths, or return `[]` when no attachments are needed.
- The final structured response is the only final delivery for the current user turn.
- Do not output internal workflow JSON.
- Do not expose internal bookkeeping such as `current_output`, `workflow_status`, or private planning notes.
- If the task is unfinished because a tool or expert failed, explain the blocker directly and say what remains.

Available skills:
{skills_summary}

Available expert agents:
{available_experts}

Expert parameter contracts:
{expert_contracts}
"""

    @staticmethod
    def _append_step_event(
        state: dict[str, Any],
        *,
        title: str,
        detail: str,
        stage: str = "orchestrating",
        session_id: str = "",
    ) -> None:
        """Append one structured orchestrator step event into session state."""
        normalized_title = title.strip() or "In Progress"
        normalized_detail = detail.strip() or "Processing the current step."
        normalized_stage = stage.strip() or "orchestrating"
        events = list(state.get("orchestration_events", []))
        events.append(
            {
                "title": normalized_title,
                "detail": normalized_detail,
                "stage": normalized_stage,
            }
        )
        state["orchestration_events"] = events
        resolved_session_id = session_id.strip() or str(state.get("sid", "")).strip()
        if resolved_session_id:
            publish_orchestration_step_event(
                session_id=resolved_session_id,
                turn_index=int(state.get("turn_index", 0) or 0),
                title=normalized_title,
                detail=normalized_detail,
                stage=normalized_stage,
            )

    @staticmethod
    def _stringify_value(value: Any, max_chars: int = 180) -> str:
        """Render one tool argument or result into a compact display string."""
        return stringify_value(value, max_chars=max_chars)

    @classmethod
    def _format_tool_args(cls, args: dict[str, Any]) -> str:
        """Format tool arguments for progress display."""
        return format_tool_args(args)

    @classmethod
    def _summarize_tool_result(cls, tool_name: str, result: Any) -> tuple[str, str]:
        """Summarize one tool result into status plus short preview."""
        return summarize_tool_result(tool_name, result)

    def _record_tool_started(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        stage: str,
    ) -> None:
        """Record one tool-call start event."""
        self._append_step_event(
            state,
            title=_DISPLAY_TOOL_TITLES.get(tool_name, tool_name),
            detail=f"Status: started\nArgs: {self._format_tool_args(args)}",
            stage=stage,
        )

    @staticmethod
    def _resolve_tool_context_session_id(tool_context: ToolContext | None) -> str:
        """Safely extract one session id from a tool context-like object."""
        session = getattr(tool_context, "session", None)
        return str(getattr(session, "id", "") or "").strip()

    def _record_tool_finished(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        stage: str,
    ) -> None:
        """Record one tool-call completion event."""
        status, summary = self._summarize_tool_result(tool_name, result)
        self._append_step_event(
            state,
            title=_DISPLAY_TOOL_TITLES.get(tool_name, tool_name),
            detail=(
                f"Status: {'success' if status == 'success' else 'error'}\n"
                f"Args: {self._format_tool_args(args)}\n"
                f"Result: {summary}"
            ),
            stage=stage,
        )

    @staticmethod
    def _record_workspace_files(
        state: dict[str, Any],
        *,
        paths: list[str],
        description: str,
        source: str,
    ) -> None:
        """Persist tool-produced workspace files into session state."""
        if not paths:
            return
        current_turn = int(state.get("turn_index", 0) or 0)
        current_step = int(state.get("step", 0) or 0)
        current_expert_step = int(state.get("expert_step", 0) or 0)
        file_records = [
            build_workspace_file_record(
                path,
                description=description,
                source=source,
                turn=current_turn,
                step=current_step,
                expert_step=current_expert_step if source == "expert" else None,
            )
            for path in paths
        ]
        generated = list(state.get("generated") or [])
        generated.extend(file_records)
        state["generated"] = generated
        history = list(state.get("files_history", []))
        history.append(file_records)
        state["new_files"] = file_records
        state["files_history"] = history

    @staticmethod
    def _advance_tool_counters(state: dict[str, Any], *, tool_name: str) -> None:
        """Advance session counters for one top-level tool or expert call."""
        state["step"] = int(state.get("step", 0) or 0) + 1
        if tool_name == "invoke_agent":
            state["expert_step"] = int(state.get("expert_step", 0) or 0) + 1

    @staticmethod
    def _normalize_generated_tool_result(
        state: dict[str, Any],
        *,
        tool_name: str,
        result: Any,
    ) -> Any:
        """Move one auto-generated builtin tool output into the standardized generated directory."""
        if tool_name not in _AUTO_OUTPUT_TOOL_NAMES or not isinstance(result, str) or result.startswith("Error"):
            return result
        try:
            relocated_path = relocate_generated_output(
                result,
                session_id=str(state.get("sid", "")).strip(),
                turn_index=int(state.get("turn_index", 0) or 0),
                step=int(state.get("step", 0) or 0),
                output_type=tool_name,
                index=0,
            )
        except Exception as exc:
            logger.warning("Failed to relocate builtin tool output for {}: {}", tool_name, exc)
            return result
        return workspace_relative_path(relocated_path)

    def _maybe_record_tool_files(
        self,
        state: dict[str, Any],
        *,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
    ) -> None:
        """Persist workspace file outputs produced by builtin tools."""
        if isinstance(result, str) and result.startswith("Error"):
            return
        if tool_name in {
            "image_crop",
            "image_rotate",
            "image_flip",
            "image_resize",
            "image_convert",
            "video_extract_frame",
            "video_trim",
            "video_concat",
            "video_convert",
            "audio_trim",
            "audio_concat",
            "audio_convert",
        } and isinstance(result, str):
            self._record_workspace_files(
                state,
                paths=[result],
                description=f"Workspace file generated by builtin tool `{tool_name}`.",
                source="builtin_tool",
            )
        elif tool_name in {"write_file", "edit_file"}:
            path = str(args.get("path", "")).strip()
            if path:
                self._record_workspace_files(
                    state,
                    paths=[path],
                    description=f"Workspace file updated by builtin tool `{tool_name}`.",
                    source="builtin_tool",
                )

    def _run_tool_with_events(
        self,
        *,
        tool_context: ToolContext | None,
        tool_name: str,
        stage: str,
        args: dict[str, Any],
        runner,
    ):
        """Execute one tool and record its start and finish events when context exists."""
        if tool_context is None:
            return runner()
        self._advance_tool_counters(tool_context.state, tool_name=tool_name)
        should_record_manually = (not step_event_streaming_active()) or tool_name not in _PLUGIN_MANAGED_TOOL_NAMES
        if should_record_manually:
            self._record_tool_started(tool_context.state, tool_name=tool_name, args=args, stage=stage)
        result = runner()
        result = self._normalize_generated_tool_result(
            tool_context.state,
            tool_name=tool_name,
            result=result,
        )
        self._maybe_record_tool_files(tool_context.state, tool_name=tool_name, args=args, result=result)
        if should_record_manually:
            self._record_tool_finished(
                tool_context.state,
                tool_name=tool_name,
                args=args,
                result=result,
                stage=stage,
            )
        return result

    def _run_builtin_tool(
        self,
        *,
        tool_context: ToolContext | None,
        tool_name: str,
        stage: str,
        args: dict[str, Any],
    ):
        """Run one BuiltinToolbox method with standard orchestrator event handling."""
        method = getattr(self.toolbox, tool_name)
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name=tool_name,
            stage=stage,
            args=args,
            runner=lambda: method(**args),
        )

    async def _run_async_tool_with_events(
        self,
        *,
        tool_context: ToolContext | None,
        tool_name: str,
        stage: str,
        args: dict[str, Any],
        runner,
    ):
        """Execute one async tool and record its lifecycle events when context exists."""
        if tool_context is None:
            return await runner()
        self._advance_tool_counters(tool_context.state, tool_name=tool_name)
        should_record_manually = (not step_event_streaming_active()) or tool_name not in _PLUGIN_MANAGED_TOOL_NAMES
        if should_record_manually:
            self._record_tool_started(tool_context.state, tool_name=tool_name, args=args, stage=stage)
        result = await runner()
        if should_record_manually:
            self._record_tool_finished(
                tool_context.state,
                tool_name=tool_name,
                args=args,
                result=result,
                stage=stage,
            )
        return result

    @staticmethod
    def build_runner_message(instruction: str) -> Content:
        """Create an ADK-compatible user message for one orchestrator turn."""
        return Content(role="user", parts=[Part(text=instruction)])

    def list_skills(self, tool_context: ToolContext | None = None) -> str:
        """List available skills in JSON format."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="list_skills",
            stage="planning",
            args={},
            runner=lambda: json.dumps(
                [
                    {
                        "name": info.name,
                        "description": info.description,
                        "source": info.source,
                    }
                    for info in self.skill_registry.list_skills()
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )

    def read_skill(self, name: str, tool_context: ToolContext | None = None) -> str:
        """Read the full markdown content of one skill."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="read_skill",
            stage="planning",
            args={"name": name},
            runner=lambda: self.skill_registry.read_skill(name),
        )

    def list_dir(self, path: str = ".", tool_context: ToolContext | None = None) -> str:
        """List one directory and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="list_dir",
            stage="inspection",
            args={"path": path},
        )

    def glob(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 200,
        entry_type: str = "files",
        tool_context: ToolContext | None = None,
    ) -> str:
        """Find workspace paths matching one glob pattern."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="glob",
            stage="inspection",
            args={
                "pattern": pattern,
                "path": path,
                "max_results": max_results,
                "entry_type": entry_type,
            },
        )

    def grep(
        self,
        pattern: str,
        path: str = ".",
        glob_pattern: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_results: int = 100,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Search workspace file contents with regex or fixed-string matching."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="grep",
            stage="inspection",
            args={
                "pattern": pattern,
                "path": path,
                "glob_pattern": glob_pattern,
                "case_insensitive": case_insensitive,
                "fixed_strings": fixed_strings,
                "output_mode": output_mode,
                "context_before": context_before,
                "context_after": context_after,
                "max_results": max_results,
            },
        )

    def read_file(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one file and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="read_file",
            stage="inspection",
            args={"path": path},
        )

    def write_file(self, path: str, content: str, tool_context: ToolContext | None = None) -> str:
        """Write one file and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="write_file",
            stage="editing",
            args={"path": path, "content": content},
        )

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Edit one file and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="edit_file",
            stage="editing",
            args={"path": path, "old_text": old_text, "new_text": new_text},
        )

    def image_crop(
        self,
        path: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Crop one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_crop",
            stage="image_processing",
            args={"path": path, "left": left, "top": top, "right": right, "bottom": bottom},
        )

    def image_rotate(
        self,
        path: str,
        degrees: float,
        expand: bool = True,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Rotate one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_rotate",
            stage="image_processing",
            args={"path": path, "degrees": degrees, "expand": expand},
        )

    def image_flip(self, path: str, direction: str, tool_context: ToolContext | None = None) -> str:
        """Flip one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_flip",
            stage="image_processing",
            args={"path": path, "direction": direction},
        )

    def image_info(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one image metadata payload and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_info",
            stage="image_processing",
            args={"path": path},
        )

    def image_resize(
        self,
        path: str,
        width: int | None = None,
        height: int | None = None,
        keep_aspect_ratio: bool = True,
        resample: str = "lanczos",
        tool_context: ToolContext | None = None,
    ) -> str:
        """Resize one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_resize",
            stage="image_processing",
            args={
                "path": path,
                "width": width,
                "height": height,
                "keep_aspect_ratio": keep_aspect_ratio,
                "resample": resample,
            },
        )

    def image_convert(
        self,
        path: str,
        output_format: str,
        mode: str | None = None,
        quality: int | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Convert one image and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="image_convert",
            stage="image_processing",
            args={"path": path, "output_format": output_format, "mode": mode, "quality": quality},
        )

    def video_info(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one video metadata payload and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_info",
            stage="video_processing",
            args={"path": path},
        )

    def video_extract_frame(
        self,
        path: str,
        timestamp: str,
        output_format: str = "png",
        tool_context: ToolContext | None = None,
    ) -> str:
        """Extract one frame from one video and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_extract_frame",
            stage="video_processing",
            args={"path": path, "timestamp": timestamp, "output_format": output_format},
        )

    def video_trim(
        self,
        path: str,
        start_time: str,
        end_time: str | None = None,
        duration: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Trim one video and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_trim",
            stage="video_processing",
            args={"path": path, "start_time": start_time, "end_time": end_time, "duration": duration},
        )

    def video_concat(
        self,
        paths: list[str],
        output_format: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Concatenate videos and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_concat",
            stage="video_processing",
            args={"paths": paths, "output_format": output_format},
        )

    def video_convert(
        self,
        path: str,
        output_format: str,
        video_codec: str | None = None,
        audio_codec: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Convert one video and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="video_convert",
            stage="video_processing",
            args={
                "path": path,
                "output_format": output_format,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
            },
        )

    def audio_info(self, path: str, tool_context: ToolContext | None = None) -> str:
        """Read one audio metadata payload and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="audio_info",
            stage="audio_processing",
            args={"path": path},
        )

    def audio_trim(
        self,
        path: str,
        start_time: str,
        end_time: str | None = None,
        duration: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Trim one audio clip and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="audio_trim",
            stage="audio_processing",
            args={"path": path, "start_time": start_time, "end_time": end_time, "duration": duration},
        )

    def audio_concat(
        self,
        paths: list[str],
        output_format: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Concatenate audio clips and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="audio_concat",
            stage="audio_processing",
            args={"paths": paths, "output_format": output_format},
        )

    def audio_convert(
        self,
        path: str,
        output_format: str,
        sample_rate: int | None = None,
        bitrate: str | None = None,
        channels: int | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Convert one audio clip and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="audio_convert",
            stage="audio_processing",
            args={
                "path": path,
                "output_format": output_format,
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "channels": channels,
            },
        )

    def exec_command(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int = 60,
        background: bool = False,
        yield_ms: int = 1000,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Execute one command and record the step."""
        scope_key = self._resolve_tool_context_session_id(tool_context) if tool_context is not None else None
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="exec_command",
            stage="execution",
            args={
                "command": command,
                "working_dir": working_dir,
                "timeout": timeout,
                "background": background,
                "yield_ms": yield_ms,
            },
            runner=lambda: self.toolbox.exec_command(
                command,
                working_dir=working_dir,
                timeout=timeout,
                background=background,
                yield_ms=yield_ms,
                scope_key=scope_key,
            ),
        )

    def process_session(
        self,
        action: str = "list",
        session_id: str | None = None,
        input_text: str = "",
        timeout_ms: int = 0,
        offset: int = 0,
        limit: int = 200,
        tool_context: ToolContext | None = None,
    ) -> str:
        """Manage background command sessions and inspect their outputs."""
        scope_key = self._resolve_tool_context_session_id(tool_context) if tool_context is not None else None
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="process_session",
            stage="execution",
            args={
                "action": action,
                "session_id": session_id,
                "input_text": input_text,
                "timeout_ms": timeout_ms,
                "offset": offset,
                "limit": limit,
            },
            runner=lambda: self.toolbox.process_session(
                action=action,
                session_id=session_id,
                input_text=input_text,
                timeout_ms=timeout_ms,
                offset=offset,
                limit=limit,
                scope_key=scope_key,
            ),
        )

    def web_search(self, query: str, count: int = 5, tool_context: ToolContext | None = None) -> str:
        """Search the web and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="web_search",
            stage="research",
            args={"query": query, "count": count},
        )

    def web_fetch(self, url: str, max_chars: int = 50000, tool_context: ToolContext | None = None) -> str:
        """Fetch one webpage and record the step."""
        return self._run_builtin_tool(
            tool_context=tool_context,
            tool_name="web_fetch",
            stage="research",
            args={"url": url, "max_chars": max_chars},
        )

    def list_session_files(self, section: str = "all", tool_context: ToolContext | None = None) -> str:
        """List normalized workspace file records already known in the current session."""
        return self._run_tool_with_events(
            tool_context=tool_context,
            tool_name="list_session_files",
            stage="inspection",
            args={"section": section},
            runner=lambda: self._list_session_files(section, tool_context=tool_context),
        )

    @staticmethod
    def _list_session_files(section: str, *, tool_context: ToolContext | None) -> str:
        """Return session-tracked file records in JSON form for one requested section."""
        if tool_context is None:
            return "Error: tool context is required to inspect session files."

        normalized_section = str(section or "all").strip().lower() or "all"
        state = tool_context.state
        uploaded = list(state.get("uploaded") or state.get("input_files") or [])
        uploaded_history = list(state.get("uploaded_history") or [])
        generated = list(state.get("generated") or [])
        generated_history = list(state.get("generated_history") or [])
        files_history = list(state.get("files_history") or [])
        latest_output_files = _latest_generated_files(state)
        payload_by_section = {
            "uploaded": {"uploaded": uploaded},
            "uploaded_history": {"uploaded_history": uploaded_history},
            "generated": {"generated": generated},
            "generated_history": {"generated_history": generated_history},
            "input": {"input_files": uploaded},
            "new": {"new_files": list(state.get("new_files") or [])},
            "latest_output": {"latest_output_files": latest_output_files},
            "history": {"files_history": files_history},
            "all": {
                "uploaded": uploaded,
                "uploaded_history": uploaded_history,
                "generated": generated,
                "generated_history": generated_history,
                "input_files": uploaded,
                "new_files": list(state.get("new_files") or []),
                "latest_output_files": latest_output_files,
                "files_history": files_history,
            },
        }
        if normalized_section not in payload_by_section:
            allowed = ", ".join(payload_by_section.keys())
            return f"Error: Unsupported section `{section}`. Allowed: {allowed}"
        return json.dumps(payload_by_section[normalized_section], ensure_ascii=False, indent=2)

    async def run_ppt_production(
        self,
        action: str,
        user_prompt: str = "",
        production_session_id: str | None = None,
        view_type: str = "overview",
        input_files: list[Any] | str | None = None,
        placeholder_assets: bool = False,
        render_settings: dict[str, Any] | None = None,
        user_response: dict[str, Any] | str | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Run, inspect, resume, view, update, revise, or analyze PPT production."""
        return await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="run_ppt_production",
            stage="ppt_processing",
            args={
                "action": action,
                "user_prompt": user_prompt,
                "production_session_id": production_session_id,
                "view_type": view_type,
                "input_files": input_files,
                "placeholder_assets": placeholder_assets,
                "render_settings": render_settings,
                "user_response": user_response,
            },
            runner=lambda: run_ppt_production_tool(
                action=action,  # type: ignore[arg-type]
                user_prompt=user_prompt,
                production_session_id=production_session_id,
                view_type=view_type,  # type: ignore[arg-type]
                input_files=input_files,
                placeholder_assets=placeholder_assets,
                render_settings=render_settings,
                user_response=user_response,
                tool_context=tool_context,
            ),
        )

    async def run_short_video_production(
        self,
        action: str,
        user_prompt: str = "",
        production_session_id: str | None = None,
        view_type: str = "overview",
        input_files: list[Any] | str | None = None,
        placeholder_assets: bool = False,
        render_settings: dict[str, Any] | None = None,
        user_response: dict[str, Any] | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Run, inspect, resume, view, update, revise, or analyze short-video production."""
        return await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="run_short_video_production",
            stage="video_processing",
            args={
                "action": action,
                "user_prompt": user_prompt,
                "production_session_id": production_session_id,
                "view_type": view_type,
                "input_files": input_files,
                "placeholder_assets": placeholder_assets,
                "render_settings": render_settings,
                "user_response": user_response,
            },
            runner=lambda: run_short_video_production_tool(
                action=action,  # type: ignore[arg-type]
                user_prompt=user_prompt,
                production_session_id=production_session_id,
                view_type=view_type,  # type: ignore[arg-type]
                input_files=input_files,
                placeholder_assets=placeholder_assets,
                render_settings=render_settings,
                user_response=user_response,
                tool_context=tool_context,
            ),
        )

    async def run_design_production(
        self,
        action: str,
        user_prompt: str = "",
        production_session_id: str | None = None,
        view_type: str = "overview",
        input_files: list[Any] | str | None = None,
        placeholder_design: bool = False,
        design_settings: dict[str, Any] | None = None,
        user_response: dict[str, Any] | str | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Run, inspect, resume, view, update, revise, or analyze Design production."""
        return await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="run_design_production",
            stage="design_processing",
            args={
                "action": action,
                "user_prompt": user_prompt,
                "production_session_id": production_session_id,
                "view_type": view_type,
                "input_files": input_files,
                "placeholder_design": placeholder_design,
                "design_settings": design_settings,
                "user_response": user_response,
            },
            runner=lambda: run_design_production_tool(
                action=action,  # type: ignore[arg-type]
                user_prompt=user_prompt,
                production_session_id=production_session_id,
                view_type=view_type,  # type: ignore[arg-type]
                input_files=input_files,
                placeholder_design=placeholder_design,
                design_settings=design_settings,
                user_response=user_response,
                tool_context=tool_context,
            ),
        )

    async def invoke_agent(
        self,
        agent_name: str,
        prompt: str,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Invoke one expert agent through the shared dispatcher."""
        invocation = await self._run_async_tool_with_events(
            tool_context=tool_context,
            tool_name="invoke_agent",
            stage="expert_execution",
            args={"agent_name": agent_name, "prompt": prompt},
            runner=lambda: dispatch_expert_call(
                agent_name=agent_name,
                prompt=prompt,
                tool_context=tool_context,
                expert_agents=self.expert_agents,
                app_name=self.app_name,
                artifact_service=self.artifact_service,
            ),
        )
        return invocation.tool_result

    async def run_agent_and_log_events(
        self,
        user_id: str,
        session_id: str,
        new_message: Optional[Content] = None,
    ) -> str:
        """Run one orchestrator turn and collect the final text response."""
        final_response_text = ""
        async for event in self.runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=new_message,
            run_config=RunConfig(max_llm_calls=SYS_CONFIG.max_iterations_orchestrator),
        ):
            logger.debug(
                "uid: {}, sid: {}, Event: {}",
                user_id,
                session_id,
                event.model_dump_json(indent=2, exclude_none=True),
            )
            if event.is_final_response() and event.content and event.content.parts:
                text_part = next((part.text for part in event.content.parts if part.text), None)
                if text_part:
                    final_response_text = text_part
        return final_response_text

    async def run_until_done(self) -> dict[str, Any]:
        """Run one orchestrator invocation and persist the structured final response."""
        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.uid,
            session_id=self.sid,
        )
        current_session.state["last_output_message"] = ""
        current_session.state["last_orchestrator_response"] = ""
        previous_event_count = len(current_session.state.get("orchestration_events", []))

        raw_final_response = await self.run_agent_and_log_events(
            user_id=self.uid,
            session_id=self.sid,
            new_message=self.build_runner_message(
                "Review the current state, use built-in tools or invoke_agent when helpful, and answer the user directly once the task is complete."
            ),
        )

        current_session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=self.uid,
            session_id=self.sid,
        )
        if current_session is None:
            raise ValueError(f"Session {self.sid} not found for user {self.uid}")

        state = current_session.state
        structured_response_payload = state.get(ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY)
        if structured_response_payload is None:
            raise ValueError(
                "Missing structured final response in session state. "
                f"Last observed final text: {raw_final_response!r}"
            )

        try:
            structured_response = OrchestratorFinalResponse.model_validate(structured_response_payload)
        except Exception as exc:
            raise ValueError(
                "Invalid structured final response payload stored in session state."
            ) from exc

        normalized_response = structured_response.reply_text.strip()
        if not normalized_response:
            raise ValueError("Structured final response must include a non-empty `reply_text`.")
        normalized_final_paths = _normalize_final_response_paths(
            structured_response.final_file_paths,
            state=state,
        )

        self._append_step_event(
            state,
            title="Finalize Result",
            detail="Preparing the final reply.",
            stage="finalizing",
            session_id=self.sid,
        )
        state_delta = {
            "workflow_status": "finished",
            "final_summary": normalized_response,
            "final_response": normalized_response,
            "final_file_paths": normalized_final_paths,
            "last_output_message": normalized_response,
            "last_orchestrator_response": normalized_response,
            "orchestration_events": list(state.get("orchestration_events", [])),
        }
        await self.session_service.append_event(
            current_session,
            Event(author="api_server", actions=EventActions(state_delta=state_delta)),
        )

        orchestration_events = list(state_delta["orchestration_events"])
        return {
            "workflow_status": "finished",
            "final_summary": normalized_response,
            "final_response": normalized_response,
            "final_file_paths": normalized_final_paths,
            "last_output_message": normalized_response,
            "new_orchestration_events": orchestration_events[previous_event_count:],
        }
