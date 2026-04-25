"""Helpers for normalizing user/model response payloads."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def normalize_user_response(response: Any) -> dict[str, Any]:
    """Return a dict payload from structured, JSON-string, or free-text input.

    ADK tool calls usually provide JSON objects for review decisions and revision
    requests, but live model runs can still send plain strings. Production logic
    treats every model-provided payload as untrusted and normalizes it before
    reading fields.
    """
    if response is None:
        return {}
    if isinstance(response, Mapping):
        return dict(response)
    if isinstance(response, str):
        return _normalize_text_response(response)
    return {"notes": str(response)}


def _normalize_text_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return _free_text_response(stripped)

    if isinstance(parsed, Mapping):
        return dict(parsed)
    if isinstance(parsed, list):
        return {"targets": parsed}
    return _free_text_response(str(parsed))


def _free_text_response(text: str) -> dict[str, Any]:
    decision = _decision_from_text(text)
    payload: dict[str, Any] = {"notes": text}
    if decision:
        payload["decision"] = decision
    return payload


def _decision_from_text(text: str) -> str:
    normalized = text.strip().lower()
    if normalized in {"approve", "approved", "yes", "ok", "okay", "可以", "确认", "同意", "批准"}:
        return "approve"
    if normalized in {"cancel", "cancelled", "stop", "取消", "停止", "放弃"}:
        return "cancel"
    if normalized in {"revise", "revision", "修改", "调整", "重做"}:
        return "revise"
    return ""
