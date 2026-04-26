"""Deterministic quality reporting for short-video production."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.production.short_video.models import (
    ShortVideoProductionState,
    ShortVideoQualityCheck,
    ShortVideoQualityReport,
)
from src.runtime.workspace import resolve_workspace_path


def build_quality_report(
    state: ShortVideoProductionState,
    *,
    report_path: str | None = None,
) -> ShortVideoQualityReport:
    """Build an explainable quality report from persisted production state."""
    checks = [
        _artifact_exists_check(state),
        _render_validation_check(state),
        _duration_check(state),
        _resolution_ratio_check(state),
        _audio_check(state),
        _segment_count_check(state),
        _subtitle_constraint_check(state),
        _reference_usage_check(state),
    ]
    checks.extend(_product_ad_checks(state))
    checks.append(_creative_summary_check(state))

    status = _aggregate_status(checks)
    recommendations = _recommendations(checks)
    summary = _summary_for(status, checks)
    artifact_path = state.render_report.output_path if state.render_report is not None else ""
    metrics = _quality_metrics(state)
    return ShortVideoQualityReport(
        status=status,
        summary=summary,
        artifact_path=artifact_path,
        report_path=report_path,
        metrics=metrics,
        checks=checks,
        recommendations=recommendations,
    )


def quality_report_markdown(report: ShortVideoQualityReport | None) -> str:
    """Render a short-video quality report as operator-readable Markdown."""
    if report is None:
        return "# Short Video Quality Report\n\nNo quality report has been generated yet.\n"
    lines = [
        "# Short Video Quality Report",
        "",
        f"- Status: {report.status}",
        f"- Summary: {report.summary}",
        f"- Artifact: {report.artifact_path or 'None'}",
        "",
        "## Metrics",
        "",
    ]
    if report.metrics:
        for key, value in report.metrics.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- No metrics available.")
    lines.extend(["", "## Checks", ""])
    for check in report.checks:
        lines.append(f"- [{check.status}] {check.check_id}: {check.summary}")
    lines.extend(["", "## Recommendations", ""])
    if report.recommendations:
        for recommendation in report.recommendations:
            lines.append(f"- {recommendation}")
    else:
        lines.append("- No immediate quality actions found.")
    return "\n".join(lines).rstrip() + "\n"


def _artifact_exists_check(state: ShortVideoProductionState) -> ShortVideoQualityCheck:
    path = state.render_report.output_path if state.render_report is not None else ""
    if not path:
        return _check(
            "artifact_exists",
            "structure",
            "fail",
            "No final render artifact is recorded.",
        )
    resolved = resolve_workspace_path(path)
    exists = resolved.exists() and resolved.is_file()
    return _check(
        "artifact_exists",
        "structure",
        "pass" if exists else "fail",
        "Final render artifact exists." if exists else "Final render artifact path is missing on disk.",
        {"path": path},
    )


def _render_validation_check(state: ShortVideoProductionState) -> ShortVideoQualityCheck:
    report = state.render_validation_report
    if report is None:
        return _check(
            "render_validation",
            "structure",
            "fail",
            "No render validation report is available.",
        )
    return _check(
        "render_validation",
        "structure",
        "pass" if report.status == "valid" else "fail",
        "Rendered MP4 is playable with video and audio streams."
        if report.status == "valid"
        else "Rendered MP4 failed validation.",
        report.model_dump(mode="json"),
    )


def _duration_check(state: ShortVideoProductionState) -> ShortVideoQualityCheck:
    expected = _expected_duration(state)
    actual = _actual_duration(state)
    if expected <= 0 or actual <= 0:
        return _check(
            "duration_match",
            "structure",
            "warning",
            "Could not compare expected and actual duration.",
            {"expected_seconds": expected, "actual_seconds": actual},
        )
    tolerance = max(0.75, expected * 0.12)
    ok = abs(actual - expected) <= tolerance
    return _check(
        "duration_match",
        "structure",
        "pass" if ok else "warning",
        "Rendered duration is close to the approved plan."
        if ok
        else "Rendered duration differs from the approved plan.",
        {"expected_seconds": expected, "actual_seconds": actual, "tolerance_seconds": round(tolerance, 2)},
    )


def _resolution_ratio_check(state: ShortVideoProductionState) -> ShortVideoQualityCheck:
    expected_ratio = _expected_ratio(state)
    width, height = _actual_size(state)
    if not expected_ratio or not width or not height:
        return _check(
            "resolution_and_ratio",
            "structure",
            "warning",
            "Could not compare rendered resolution and target ratio.",
            {"expected_ratio": expected_ratio, "width": width, "height": height},
        )
    expected_value = _ratio_value(expected_ratio)
    actual_value = width / height
    ok = abs(actual_value - expected_value) <= 0.04
    return _check(
        "resolution_and_ratio",
        "structure",
        "pass" if ok else "warning",
        "Rendered resolution matches the selected aspect ratio."
        if ok
        else "Rendered resolution does not match the selected aspect ratio closely.",
        {
            "expected_ratio": expected_ratio,
            "width": width,
            "height": height,
            "actual_ratio": round(actual_value, 4),
        },
    )


def _audio_check(state: ShortVideoProductionState) -> ShortVideoQualityCheck:
    has_audio = bool(state.render_validation_report and state.render_validation_report.has_audio)
    requires_voice = _requires_voice_or_audio(state)
    valid_audio_assets = [item for item in state.audio_manifest if item.status == "valid"]
    ok = has_audio and (not requires_voice or bool(valid_audio_assets))
    if ok:
        summary = "Audio stream is present and production state contains valid audio assets."
    elif requires_voice:
        summary = "The brief asks for voice/audio, but no valid audio output is confirmed."
    else:
        summary = "No audio stream is confirmed in the rendered MP4."
    return _check(
        "audio_or_voiceover",
        "business",
        "pass" if ok else "warning",
        summary,
        {
            "requires_voice_or_audio": requires_voice,
            "has_audio_stream": has_audio,
            "valid_audio_assets": len(valid_audio_assets),
        },
    )


def _segment_count_check(state: ShortVideoProductionState) -> ShortVideoQualityCheck:
    if not state.shot_asset_plans:
        return _check(
            "shot_segment_count",
            "structure",
            "not_applicable",
            "No shot-segment plan was used for this production path.",
        )
    approved = [item for item in state.shot_artifacts if item.status == "approved"]
    ok = len(approved) == len(state.shot_asset_plans)
    return _check(
        "shot_segment_count",
        "structure",
        "pass" if ok else "warning",
        "All planned shot segments have approved generated previews."
        if ok
        else "Some planned shot segments are not approved yet.",
        {"planned_segments": len(state.shot_asset_plans), "approved_segments": len(approved)},
    )


def _subtitle_constraint_check(state: ShortVideoProductionState) -> ShortVideoQualityCheck:
    requested = _requests_no_subtitles(_combined_text(state))
    if not requested:
        return _check(
            "no_subtitles_constraint",
            "business",
            "not_applicable",
            "The brief did not explicitly request no subtitles.",
        )
    subtitle_artifacts = [
        item.path
        for item in state.artifacts
        if _looks_like_subtitle_path(item.path) or _looks_like_subtitle_text(item.description)
    ]
    ok = not subtitle_artifacts
    return _check(
        "no_subtitles_constraint",
        "business",
        "pass" if ok else "warning",
        "No subtitle or caption artifact is present in production state."
        if ok
        else "Subtitle-like artifacts are present despite a no-subtitle request.",
        {"subtitle_artifacts": subtitle_artifacts},
    )


def _reference_usage_check(state: ShortVideoProductionState) -> ShortVideoQualityCheck:
    valid_references = [item.reference_asset_id for item in state.reference_assets if item.status == "valid"]
    if not valid_references:
        return _check(
            "reference_usage_declared",
            "business",
            "not_applicable",
            "No user reference assets were provided.",
        )
    declared = set()
    if state.storyboard is not None:
        declared.update(state.storyboard.reference_asset_ids)
        for shot in state.storyboard.shots:
            declared.update(shot.reference_asset_ids)
    if state.asset_plan is not None:
        declared.update(state.asset_plan.reference_asset_ids)
    for plan in state.shot_asset_plans:
        declared.update(plan.reference_asset_ids)
    missing = [item for item in valid_references if item not in declared]
    return _check(
        "reference_usage_declared",
        "business",
        "pass" if not missing else "warning",
        "All valid reference assets are declared in storyboard or asset plans."
        if not missing
        else "Some valid reference assets are not declared in storyboard or asset plans.",
        {"valid_reference_asset_ids": valid_references, "missing_reference_asset_ids": missing},
    )


def _product_ad_checks(state: ShortVideoProductionState) -> list[ShortVideoQualityCheck]:
    if not (state.asset_plan and state.asset_plan.video_type == "product_ad"):
        return [
            _check(
                "product_exposure",
                "business",
                "not_applicable",
                "The production is not classified as a product ad.",
            ),
            _check(
                "product_benefit_coverage",
                "business",
                "not_applicable",
                "The production is not classified as a product ad.",
            ),
            _check(
                "product_cta",
                "business",
                "not_applicable",
                "The production is not classified as a product ad.",
            ),
        ]
    combined = _combined_text(state)
    exposure_ok = bool(state.reference_assets) or any(
        token in combined.lower()
        for token in ("product", "brand", "产品", "商品", "包装", "品牌")
    )
    benefits = _extract_product_benefit_terms(state.brief_summary)
    covered = [term for term in benefits if term and term in combined]
    cta_ok = any(
        token in combined.lower()
        for token in ("cta", "buy", "shop", "order", "购买", "下单", "点击", "咨询", "关注", "推荐", "来一口")
    )
    return [
        _check(
            "product_exposure",
            "business",
            "pass" if exposure_ok else "warning",
            "Product or brand exposure is represented in the plan."
            if exposure_ok
            else "Product or brand exposure is not clearly represented in the plan.",
            {"has_reference_assets": bool(state.reference_assets)},
        ),
        _check(
            "product_benefit_coverage",
            "business",
            "pass" if not benefits or covered else "warning",
            "Product benefit terms from the brief are covered in the plan."
            if not benefits or covered
            else "Product benefit terms from the brief are not clearly covered in the plan.",
            {"benefit_terms": benefits, "covered_terms": covered},
        ),
        _check(
            "product_cta",
            "business",
            "pass" if cta_ok else "warning",
            "The plan includes an action or recommendation cue."
            if cta_ok
            else "No clear call-to-action or recommendation cue was detected.",
        ),
    ]


def _creative_summary_check(state: ShortVideoProductionState) -> ShortVideoQualityCheck:
    storyboard_shots = len(state.storyboard.shots) if state.storyboard is not None else 0
    prompts = [plan.visual_prompt for plan in state.shot_asset_plans if plan.visual_prompt]
    return _check(
        "creative_summary",
        "creative",
        "pass" if storyboard_shots or prompts else "warning",
        "Creative structure is available through storyboard shots and provider prompts."
        if storyboard_shots or prompts
        else "No storyboard or provider prompt structure is available for creative review.",
        {"storyboard_shots": storyboard_shots, "provider_prompt_count": len(prompts)},
    )


def _quality_metrics(state: ShortVideoProductionState) -> dict[str, Any]:
    width, height = _actual_size(state)
    return {
        "duration_seconds": _actual_duration(state),
        "expected_duration_seconds": _expected_duration(state),
        "width": width,
        "height": height,
        "expected_ratio": _expected_ratio(state),
        "has_audio": bool(state.render_validation_report and state.render_validation_report.has_audio),
        "shot_segments": len(state.shot_asset_plans),
        "approved_shot_segments": len([item for item in state.shot_artifacts if item.status == "approved"]),
        "reference_assets": len([item for item in state.reference_assets if item.status == "valid"]),
    }


def _check(
    check_id: str,
    category: str,
    status: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> ShortVideoQualityCheck:
    return ShortVideoQualityCheck(
        check_id=check_id,
        category=category,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        summary=summary,
        details=details or {},
    )


def _aggregate_status(checks: list[ShortVideoQualityCheck]) -> str:
    if any(item.status == "fail" for item in checks):
        return "fail"
    if any(item.status == "warning" for item in checks):
        return "warning"
    return "pass"


def _recommendations(checks: list[ShortVideoQualityCheck]) -> list[str]:
    recommendations: list[str] = []
    for check in checks:
        if check.status not in {"warning", "fail"}:
            continue
        recommendations.append(f"{check.check_id}: {check.summary}")
    return recommendations


def _summary_for(status: str, checks: list[ShortVideoQualityCheck]) -> str:
    failed = len([item for item in checks if item.status == "fail"])
    warnings = len([item for item in checks if item.status == "warning"])
    passed = len([item for item in checks if item.status == "pass"])
    if status == "fail":
        return f"Quality report found {failed} failing check(s), {warnings} warning(s), and {passed} passing check(s)."
    if status == "warning":
        return f"Quality report found {warnings} warning(s) and {passed} passing check(s)."
    return f"Quality report passed {passed} check(s) with no blocking issues."


def _expected_duration(state: ShortVideoProductionState) -> float:
    if state.timeline is not None and state.timeline.duration_seconds > 0:
        return float(state.timeline.duration_seconds)
    if state.asset_plan is not None:
        return float(state.asset_plan.duration_seconds)
    return 0.0


def _actual_duration(state: ShortVideoProductionState) -> float:
    if state.render_validation_report is not None and state.render_validation_report.duration_seconds > 0:
        return float(state.render_validation_report.duration_seconds)
    if state.render_report is not None:
        return float(state.render_report.duration_seconds)
    return 0.0


def _expected_ratio(state: ShortVideoProductionState) -> str:
    if state.timeline is not None:
        return state.timeline.render_settings.aspect_ratio
    if state.asset_plan is not None and state.asset_plan.selected_ratio:
        return state.asset_plan.selected_ratio
    if state.storyboard is not None and state.storyboard.selected_ratio:
        return state.storyboard.selected_ratio
    return ""


def _actual_size(state: ShortVideoProductionState) -> tuple[int | None, int | None]:
    if state.render_validation_report is not None:
        return state.render_validation_report.width, state.render_validation_report.height
    if state.render_report is not None:
        return state.render_report.width, state.render_report.height
    return None, None


def _ratio_value(ratio: str) -> float:
    left, right = ratio.split(":", 1)
    return float(left) / float(right)


def _requires_voice_or_audio(state: ShortVideoProductionState) -> bool:
    text = _combined_text(state).lower()
    if any(
        token in text
        for token in (
            "语音",
            "声音",
            "口播",
            "旁白",
            "对白",
            "对话",
            "有声",
            "音效",
            "音乐",
            "voice",
            "voiceover",
            "dialogue",
            "spoken",
            "audio",
            "tts",
        )
    ):
        return True
    return bool(state.asset_plan and (state.asset_plan.planned_generate_audio or state.asset_plan.planned_tts))


def _combined_text(state: ShortVideoProductionState) -> str:
    pieces = [state.brief_summary]
    if state.storyboard is not None:
        pieces.extend(state.storyboard.global_constraints)
        pieces.append(state.storyboard.narrative_summary)
        for shot in state.storyboard.shots:
            pieces.extend([shot.purpose, shot.visual_beat, shot.audio_notes])
            pieces.extend(shot.dialogue_lines)
            pieces.extend(shot.constraints)
    if state.asset_plan is not None:
        pieces.extend([state.asset_plan.shot_plan.visual_prompt, state.asset_plan.shot_plan.voiceover_text])
    for plan in state.shot_asset_plans:
        pieces.extend([plan.visual_prompt, plan.voiceover_text])
    return "\n".join(str(item or "") for item in pieces)


def _requests_no_subtitles(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(
        token in normalized
        for token in ("不用显示字幕", "不要字幕", "无字幕", "no subtitles", "no captions")
    )


def _looks_like_subtitle_path(path: str) -> bool:
    suffix = Path(str(path or "")).suffix.lower()
    return suffix in {".srt", ".vtt", ".ass", ".ssa"}


def _looks_like_subtitle_text(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(token in normalized for token in ("subtitle", "caption", "字幕"))


def _extract_product_benefit_terms(brief: str) -> list[str]:
    candidates: list[str] = []
    for pattern in (
        r"(?:卖点|主要卖点|店长推荐理由|强调|突出|重点)[是包括：:\s]*([^。\n；;]+)",
        r"(?:benefits?|selling points?)[：:\s]*([^\n.]+)",
    ):
        for match in re.finditer(pattern, str(brief or ""), flags=re.IGNORECASE):
            candidates.extend(_split_terms(match.group(1)))
    fallback_terms = [
        "高含肉",
        "无谷",
        "适口性",
        "安心",
        "自然",
        "健康",
        "全猫",
        "粗蛋白",
        "omega",
        "premium",
        "healthy",
    ]
    lower_brief = str(brief or "").lower()
    candidates.extend(term for term in fallback_terms if term.lower() in lower_brief)
    deduped: list[str] = []
    for candidate in candidates:
        cleaned = candidate.strip()
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped[:8]


def _split_terms(text: str) -> list[str]:
    return [
        item.strip(" ，,、；;。.")
        for item in re.split(r"[，,、；;]", str(text or ""))
        if item.strip(" ，,、；;。.")
    ]
