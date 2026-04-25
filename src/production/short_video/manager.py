"""Runtime service for short-video production."""

from __future__ import annotations

import json
from typing import Any

from src.production.errors import ProductionError as ProductionRuntimeError
from src.production.errors import ProductionSessionNotFoundError
from src.production.models import (
    ProductionErrorInfo,
    ProductionEvent,
    ProductionOwnerRef,
    ProductionRunResult,
    WorkspaceFileRef,
    new_id,
)
from src.production.session_store import ProductionSessionStore
from src.production.short_video.models import (
    AudioClip,
    AudioTrack,
    ReferenceAssetEntry,
    ShortVideoProductionState,
    ShortVideoRenderSettings,
    ShortVideoTimeline,
    VideoClip,
    VideoTrack,
)
from src.production.short_video.placeholders import PlaceholderAssetFactory
from src.production.short_video.renderer import TimelineRenderer
from src.production.short_video.validators import RenderValidator
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


class ShortVideoProductionManager:
    """Coordinate short-video production state, rendering, and projection."""

    capability = "short_video"

    def __init__(
        self,
        *,
        store: ProductionSessionStore | None = None,
        placeholder_factory: PlaceholderAssetFactory | None = None,
        renderer: TimelineRenderer | None = None,
        validator: RenderValidator | None = None,
    ) -> None:
        """Initialize the short-video production manager."""
        self.store = store or ProductionSessionStore()
        self.placeholder_factory = placeholder_factory or PlaceholderAssetFactory()
        self.renderer = renderer or TimelineRenderer()
        self.validator = validator or RenderValidator()

    def start(
        self,
        *,
        user_prompt: str,
        input_files: list[dict[str, Any]],
        placeholder_assets: bool,
        render_settings: dict[str, Any] | None,
        adk_state,
    ) -> ProductionRunResult:
        """Start a P0a short-video production run."""
        context = _context_from_adk_state(adk_state)
        production_session = self.store.create_session(
            capability=self.capability,
            adk_session_id=context["sid"],
            turn_index=context["turn_index"],
            owner_ref=context["owner_ref"],
        )
        state = ShortVideoProductionState(
            production_session=production_session,
            status="running",
            stage="initializing",
            progress_percent=5,
            brief_summary=_brief_summary(user_prompt),
            reference_assets=_reference_assets_from_input_files(
                input_files=input_files,
                turn_index=context["turn_index"],
            ),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="production_started",
                stage=state.stage,
                message="Short-video production session started.",
            )
        )

        try:
            if not placeholder_assets:
                state.status = "needs_user_input"
                state.stage = "awaiting_p0a_mode"
                state.progress_percent = 10
                state.production_events.append(
                    ProductionEvent(
                        event_type="needs_user_input",
                        stage=state.stage,
                        message="P0a currently requires placeholder_assets=true.",
                    )
                )
                self._save_projection_files(state)
                self.store.save_state(state)
                return self._result_from_state(
                    state,
                    message="P0a currently supports only placeholder rendering. Please retry with placeholder_assets=true.",
                )

            session_root = self.store.session_root(production_session)
            settings = _normalize_render_settings(render_settings or {})
            duration_seconds = float((render_settings or {}).get("duration_seconds") or 4.0)
            state.asset_manifest, state.audio_manifest = self.placeholder_factory.create(
                session_root=session_root,
                render_settings=settings,
                duration_seconds=duration_seconds,
            )
            state.stage = "timeline_prepared"
            state.progress_percent = 45
            state.timeline = _build_placeholder_timeline(
                asset_id=_first_video_asset_id(state),
                audio_id=_first_audio_id(state),
                render_settings=settings,
                duration_seconds=duration_seconds,
            )
            state.production_events.append(
                ProductionEvent(
                    event_type="placeholder_assets_created",
                    stage=state.stage,
                    message="Created placeholder video, image, and silent audio assets.",
                )
            )

            final_path = session_root / "final" / "final.mp4"
            state.stage = "rendering"
            state.progress_percent = 70
            state.render_report = self.renderer.render(
                timeline=state.timeline,
                asset_manifest=state.asset_manifest,
                audio_manifest=state.audio_manifest,
                output_path=final_path,
            )
            state.stage = "quality_check"
            state.progress_percent = 90
            state.render_validation_report = self.validator.validate(state.render_report.output_path)
            if state.render_validation_report.status != "valid":
                raise RuntimeError("; ".join(state.render_validation_report.issues) or "render validation failed")

            state.status = "completed"
            state.stage = "completed"
            state.progress_percent = 100
            state.artifacts = [
                WorkspaceFileRef(
                    name="final.mp4",
                    path=state.render_report.output_path,
                    description="P0a placeholder short-video render.",
                    source=self.capability,
                )
            ]
            state.production_events.append(
                ProductionEvent(
                    event_type="production_completed",
                    stage=state.stage,
                    message="Placeholder short-video render completed.",
                    metadata={"artifact_path": state.render_report.output_path},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message="P0a placeholder short-video production completed.",
            )
        except Exception as exc:
            state.status = "failed"
            state.stage = "failed"
            state.production_events.append(
                ProductionEvent(
                    event_type="production_failed",
                    stage=state.stage,
                    message=f"Short-video P0a production failed: {type(exc).__name__}: {exc}",
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            return self._result_from_state(
                state,
                message="Short-video P0a production failed.",
                error=ProductionErrorInfo(
                    code="short_video_p0a_failed",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )

    def status(
        self,
        *,
        production_session_id: str | None,
        adk_state,
    ) -> ProductionRunResult:
        """Return a read-only status snapshot for a short-video production run."""
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            state = self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=ShortVideoProductionState,
            )
        except ProductionSessionNotFoundError:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=session_id or "",
                stage="not_found",
                progress_percent=0,
                message="Production session was not found or is not owned by this conversation.",
                error=ProductionErrorInfo(
                    code="production_session_not_found_or_not_owned",
                    message="Production session was not found or is not owned by this conversation.",
                ),
            )
        return self._result_from_state(state, message=_status_message(state))

    def resume(
        self,
        *,
        production_session_id: str | None,
        user_response: dict[str, Any] | None,
        adk_state,
    ) -> ProductionRunResult:
        """Handle a P0a resume request without running real revision logic."""
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            state = self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=ShortVideoProductionState,
            )
        except ProductionRuntimeError:
            return self.status(production_session_id=session_id, adk_state=adk_state)
        state.production_events.append(
            ProductionEvent(
                event_type="resume_not_supported",
                stage=state.stage,
                message="P0a resume is not implemented yet.",
                metadata={"user_response": user_response or {}},
            )
        )
        self.store.save_state(state)
        return self._result_from_state(
            state,
            message="P0a can report status, but review/resume is reserved for P0c.",
        )

    def _result_from_state(
        self,
        state: ShortVideoProductionState,
        *,
        message: str,
        error: ProductionErrorInfo | None = None,
    ) -> ProductionRunResult:
        state_ref = f"{state.production_session.root_dir}/state.json"
        return ProductionRunResult(
            status=state.status,
            capability=self.capability,
            production_session_id=state.production_session.production_session_id,
            stage=state.stage,
            progress_percent=state.progress_percent,
            message=message,
            state_ref=state_ref,
            artifacts=state.artifacts,
            review_payload=(
                state.active_breakpoint.review_payload
                if state.active_breakpoint is not None
                else None
            ),
            error=error,
            events=state.production_events[-5:],
        )

    def _save_projection_files(self, state: ShortVideoProductionState) -> None:
        """Write human-readable projection files derived from ProductionState."""
        root = self.store.session_root(state.production_session)
        (root / "brief.md").write_text(
            f"# Short Video Brief\n\n{state.brief_summary}\n",
            encoding="utf-8",
        )
        (root / "asset_plan.json").write_text(
            json.dumps(
                {
                    "reference_assets": [item.model_dump(mode="json") for item in state.reference_assets],
                    "asset_manifest": [item.model_dump(mode="json") for item in state.asset_manifest],
                    "audio_manifest": [item.model_dump(mode="json") for item in state.audio_manifest],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if state.timeline is not None:
            (root / "timeline.json").write_text(
                state.timeline.model_dump_json(indent=2),
                encoding="utf-8",
            )


def _context_from_adk_state(adk_state) -> dict[str, Any]:
    sid = str(adk_state.get("sid", "") or "").strip() or "default"
    turn_index = int(adk_state.get("turn_index", 0) or 0)
    return {
        "sid": sid,
        "turn_index": turn_index,
        "owner_ref": ProductionOwnerRef(
            channel=str(adk_state.get("channel", "") or "").strip(),
            chat_id=str(adk_state.get("chat_id", "") or "").strip(),
            sender_id=str(adk_state.get("sender_id", "") or "").strip(),
        ),
    }


def _resolve_requested_session_id(production_session_id: str | None, adk_state) -> str:
    requested = str(production_session_id or "").strip()
    if requested:
        return requested
    return str(adk_state.get("active_production_session_id", "") or "").strip()


def _brief_summary(user_prompt: str) -> str:
    prompt = str(user_prompt or "").strip()
    return prompt or "P0a placeholder short-video production."


def _reference_assets_from_input_files(
    *,
    input_files: list[dict[str, Any]],
    turn_index: int,
) -> list[ReferenceAssetEntry]:
    references: list[ReferenceAssetEntry] = []
    for file_info in input_files:
        path = str(file_info.get("path", "") or "").strip()
        if not path:
            continue
        references.append(
            ReferenceAssetEntry(
                reference_asset_id=new_id("reference"),
                path=workspace_relative_path(resolve_workspace_path(path)),
                added_turn_index=turn_index,
                metadata={
                    "name": str(file_info.get("name", "") or "").strip(),
                    "description": str(file_info.get("description", "") or "").strip(),
                },
            )
        )
    return references


def _normalize_render_settings(payload: dict[str, Any]) -> ShortVideoRenderSettings:
    aspect_ratio = str(payload.get("aspect_ratio", "16:9") or "16:9").strip()
    if aspect_ratio not in {"16:9", "9:16", "1:1"}:
        aspect_ratio = "16:9"
    default_dimensions = {
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "1:1": (1024, 1024),
    }
    default_width, default_height = default_dimensions[aspect_ratio]
    width = _positive_int(payload.get("width"), default_width)
    height = _positive_int(payload.get("height"), default_height)
    fps = _positive_int(payload.get("fps"), 24)
    return ShortVideoRenderSettings(
        aspect_ratio=aspect_ratio,  # type: ignore[arg-type]
        width=width,
        height=height,
        fps=fps,
    )


def _build_placeholder_timeline(
    *,
    asset_id: str,
    audio_id: str,
    render_settings: ShortVideoRenderSettings,
    duration_seconds: float,
) -> ShortVideoTimeline:
    return ShortVideoTimeline(
        timeline_id=new_id("timeline"),
        duration_seconds=duration_seconds,
        render_settings=render_settings,
        video_tracks=[
            VideoTrack(
                track_id="video_main",
                clips=[
                    VideoClip(
                        clip_id="clip_placeholder_video",
                        asset_id=asset_id,
                        start_seconds=0,
                        duration_seconds=duration_seconds,
                    )
                ],
            )
        ],
        audio_tracks=[
            AudioTrack(
                track_id="audio_main",
                kind="silent",
                clips=[
                    AudioClip(
                        clip_id="clip_placeholder_audio",
                        audio_id=audio_id,
                        start_seconds=0,
                        duration_seconds=duration_seconds,
                    )
                ],
            )
        ],
    )


def _first_video_asset_id(state: ShortVideoProductionState) -> str:
    for asset in state.asset_manifest:
        if asset.kind == "video":
            return asset.asset_id
    raise ValueError("Placeholder factory did not create a video asset.")


def _first_audio_id(state: ShortVideoProductionState) -> str:
    for audio in state.audio_manifest:
        return audio.audio_id
    raise ValueError("Placeholder factory did not create an audio asset.")


def _positive_int(value: Any, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _status_message(state: ShortVideoProductionState) -> str:
    return (
        f"Short-video production `{state.production_session.production_session_id}` "
        f"is {state.status} at stage `{state.stage}` ({state.progress_percent}%)."
    )

