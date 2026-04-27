"""Shared display helpers for tool-call progress messages."""

from __future__ import annotations

import json
import re
from typing import Any


def stringify_value(value: Any, max_chars: int = 180) -> str:
    """Render one tool argument or result into a compact display string."""
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = text.replace("\n", "\\n")
    if len(text) > max_chars:
        text = f"{text[: max_chars - 3].rstrip()}..."
    return text or "(empty)"


def format_tool_args(args: dict[str, Any]) -> str:
    """Format tool arguments for progress display."""
    if not args:
        return "(no args)"
    parts = [f"{key}={stringify_value(value, max_chars=120)}" for key, value in args.items()]
    return "; ".join(parts)


def preview_lines(text: str, *, max_lines: int = 3, max_chars: int = 180) -> str:
    """Build a short multi-line preview from plain text."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    preview = " | ".join(lines[:max_lines]) if lines else text.strip()
    return stringify_value(preview, max_chars=max_chars)


def head_tail_preview(text: str, *, max_lines: int = 2, max_chars: int = 220) -> str:
    """Build one head/tail preview from plain text."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "(empty)"
    head = " | ".join(lines[:max_lines])
    tail = " | ".join(lines[-max_lines:]) if len(lines) > max_lines else ""
    if tail and tail != head:
        preview = f"Start: {head} || End: {tail}"
    else:
        preview = f"Start: {head}"
    return stringify_value(preview, max_chars=max_chars)


def _summarize_list_dir_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("Warning"):
        return result_text
    entries = [line.strip() for line in result_text.splitlines() if line.strip()]
    preview = "; ".join(entries[:3])
    return f"{len(entries)} entries. Preview: {stringify_value(preview, max_chars=180)}"


def _summarize_read_file_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("Warning"):
        return result_text
    char_count = len(result_text)
    line_count = len(result_text.splitlines()) or 1
    preview = head_tail_preview(result_text, max_lines=2, max_chars=220)
    return f"Read succeeded, about {char_count} characters across {line_count} lines. {preview}"


def _summarize_exec_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("Warning"):
        return result_text
    if result_text.startswith("Command still running (session "):
        match = re.search(r"session ([^,\s]+), pid ([^)]+)\)", result_text)
        if match:
            session_id, pid = match.groups()
            return f"Background command started in session {session_id} with pid {pid}."
        return stringify_value(result_text, max_chars=220)
    stdout_text = result_text
    stderr_text = ""
    if "\nSTDERR:\n" in result_text:
        stdout_text, stderr_text = result_text.split("\nSTDERR:\n", 1)
    elif result_text.startswith("STDERR:\n"):
        stdout_text = ""
        stderr_text = result_text[len("STDERR:\n") :]

    stdout_lines = [line for line in stdout_text.splitlines() if line.strip()]
    stderr_lines = [line for line in stderr_text.splitlines() if line.strip()]
    parts = [f"Command completed, about {len(stdout_lines)} stdout lines"]
    if stdout_lines:
        parts.append(f"stdout summary: {head_tail_preview(stdout_text, max_lines=2, max_chars=180)}")
    if stderr_text.strip() or stderr_lines:
        parts.append(f"about {len(stderr_lines)} stderr lines")
        parts.append(f"stderr summary: {head_tail_preview(stderr_text, max_lines=2, max_chars=180)}")
    return "; ".join(parts)


def _summarize_web_search_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("No results") or result_text.startswith("Warning"):
        return result_text
    result_count = sum(1 for line in result_text.splitlines() if re.match(r"^\d+\.\s", line.strip()))
    preview = preview_lines(result_text, max_lines=4, max_chars=180)
    return f"Search completed with {result_count} results. Summary: {preview}"


def _summarize_web_fetch_result(result_text: str) -> str:
    try:
        payload = json.loads(result_text)
    except Exception:
        return stringify_value(result_text, max_chars=220)

    if not isinstance(payload, dict):
        return stringify_value(result_text, max_chars=220)
    if payload.get("error"):
        return f"Fetch failed: {payload.get('error')}"
    text = str(payload.get("text", "")).strip()
    extractor = str(payload.get("extractor", "")).strip() or "unknown"
    length = payload.get("length", len(text))
    preview = head_tail_preview(text, max_lines=2, max_chars=220)
    return f"Fetch succeeded, extractor={extractor}, body about {length} characters. {preview}"


def _summarize_write_like_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("Warning"):
        return result_text
    return stringify_value(result_text, max_chars=200)


def _summarize_json_metadata_result(result_text: str) -> str:
    try:
        payload = json.loads(result_text)
    except Exception:
        return stringify_value(result_text, max_chars=220)
    if not isinstance(payload, dict):
        return stringify_value(result_text, max_chars=220)
    preview_items = []
    for key in ("format", "width", "height", "mode", "duration_seconds", "fps", "codec", "video_codec", "audio_codec", "sample_rate", "channels"):
        if key in payload:
            preview_items.append(f"{key}={payload[key]}")
    if not preview_items:
        preview_items = [f"{key}={value}" for key, value in list(payload.items())[:4]]
    return f"Metadata loaded. Preview: {stringify_value('; '.join(preview_items), max_chars=200)}"


def _summarize_glob_result(result_text: str) -> str:
    if (
        result_text.startswith("Error")
        or result_text.startswith("Warning")
        or result_text.startswith("No paths matched")
    ):
        return result_text
    matches = [line.strip() for line in result_text.splitlines() if line.strip()]
    preview = "; ".join(matches[:3])
    return f"Found {len(matches)} matching paths. Preview: {stringify_value(preview, max_chars=180)}"


def _summarize_grep_result(result_text: str) -> str:
    if (
        result_text.startswith("Error")
        or result_text.startswith("Warning")
        or result_text.startswith("No matches found")
    ):
        return result_text
    if "\n\nStatus:" in result_text:
        output_text, status_text = result_text.split("\n\nStatus:", 1)
        preview = head_tail_preview(output_text, max_lines=2, max_chars=180)
        return f"Session update received. Status:{status_text.strip()}. Output: {preview}"
    lines = [line.strip() for line in result_text.splitlines() if line.strip()]
    if any(re.match(r"^[^:\n]+:\d+$", line) for line in lines):
        preview = head_tail_preview(result_text, max_lines=3, max_chars=220)
        return f"Matched content snippets. {preview}"
    preview = "; ".join(lines[:3])
    return f"Found matches in {len(lines)} files or entries. Preview: {stringify_value(preview, max_chars=180)}"


def _summarize_process_result(result_text: str) -> str:
    if (
        result_text.startswith("Error")
        or result_text.startswith("Warning")
        or result_text.startswith("No running or recent sessions.")
    ):
        return result_text
    if result_text.startswith("Removed session") or result_text.startswith("Kill signal sent"):
        return stringify_value(result_text, max_chars=200)
    if "\n\nStatus:" in result_text:
        output_text, status_text = result_text.split("\n\nStatus:", 1)
        preview = head_tail_preview(output_text, max_lines=2, max_chars=180)
        return f"Session update received. Status:{status_text.strip()}. Output: {preview}"
    sessions = [line.strip() for line in result_text.splitlines() if line.strip()]
    preview = "; ".join(sessions[:2])
    return f"Listed {len(sessions)} sessions. Preview: {stringify_value(preview, max_chars=180)}"


def _summarize_invoke_agent_result(result: Any) -> str:
    if isinstance(result, dict):
        agent_name = str(result.get("agent_name", "")).strip() or "expert"
        status = str(result.get("status", "")).strip() or "unknown"
        message = stringify_value(result.get("message", ""), max_chars=180)
        output_files = result.get("output_files") or []
        output_text = str(result.get("output_text", "")).strip()
        parts = [f"{agent_name} finished with status={status}", f"message={message}"]
        if output_files:
            parts.append(f"files={len(output_files)}")
        if output_text:
            parts.append(f"text={head_tail_preview(output_text, max_lines=2, max_chars=160)}")
        return "; ".join(parts)
    return stringify_value(result, max_chars=220)


def _summarize_production_result(result: Any) -> str:
    if isinstance(result, dict):
        status = str(result.get("status", "")).strip() or "unknown"
        capability = str(result.get("capability", "")).strip() or "production"
        stage = str(result.get("stage", "")).strip() or "unknown"
        progress = result.get("progress_percent", 0)
        message = stringify_value(result.get("message", ""), max_chars=180)
        artifacts = result.get("artifacts") or []
        view = result.get("view") or {}
        parts = [
            f"{capability} status={status}",
            f"stage={stage}",
            f"progress={progress}%",
            f"message={message}",
        ]
        if artifacts:
            parts.append(f"artifacts={len(artifacts)}")
        if isinstance(view, dict) and view:
            parts.append(f"view={stringify_value(view.get('view_type', 'available'), max_chars=40)}")
        return "; ".join(parts)
    return stringify_value(result, max_chars=220)


def _summarize_list_session_files_result(result_text: str) -> str:
    try:
        payload = json.loads(result_text)
    except Exception:
        return stringify_value(result_text, max_chars=220)

    if not isinstance(payload, dict):
        return stringify_value(result_text, max_chars=220)

    for key in ("uploaded", "generated", "input_files", "new_files", "latest_output_files"):
        if key in payload and len(payload) == 1:
            files = payload.get(key)
            if isinstance(files, list):
                preview = "; ".join(
                    str(file_info.get("path", "")).strip()
                    for file_info in files[:3]
                    if isinstance(file_info, dict) and str(file_info.get("path", "")).strip()
                )
                return (
                    f"{key} contains {len(files)} record(s). "
                    f"Preview: {stringify_value(preview, max_chars=180)}"
                )

    for key in ("uploaded_history", "generated_history"):
        if key in payload and len(payload) == 1:
            history = payload.get(key)
            if isinstance(history, list):
                preview = "; ".join(
                    f"turn={entry.get('turn')}, files={len(entry.get('files') or [])}"
                    for entry in history[:3]
                    if isinstance(entry, dict)
                )
                return (
                    f"{key} contains {len(history)} turn group(s). "
                    f"Preview: {stringify_value(preview, max_chars=180)}"
                )

    if "files_history" in payload and len(payload) == 1:
        history = payload.get("files_history")
        if isinstance(history, list):
            return f"Session file history contains {len(history)} step group(s)."

    uploaded_count = len(payload.get("uploaded") or []) if isinstance(payload.get("uploaded"), list) else 0
    generated_count = len(payload.get("generated") or []) if isinstance(payload.get("generated"), list) else 0
    input_count = len(payload.get("input_files") or []) if isinstance(payload.get("input_files"), list) else 0
    new_count = len(payload.get("new_files") or []) if isinstance(payload.get("new_files"), list) else 0
    latest_count = (
        len(payload.get("latest_output_files") or [])
        if isinstance(payload.get("latest_output_files"), list)
        else 0
    )
    history_count = len(payload.get("files_history") or []) if isinstance(payload.get("files_history"), list) else 0
    return (
        "Session file snapshot loaded. "
        f"uploaded={uploaded_count}; generated={generated_count}; input={input_count}; "
        f"new={new_count}; latest_output={latest_count}; "
        f"history_steps={history_count}."
    )


def summarize_tool_result(tool_name: str, result: Any) -> tuple[str, str]:
    """Summarize one tool result into status plus short preview."""
    if tool_name in {"run_short_video_production", "run_ppt_production"}:
        status = "success"
        if isinstance(result, dict) and str(result.get("status", "")).strip().lower() == "failed":
            status = "error"
        return status, _summarize_production_result(result)

    if tool_name == "invoke_agent":
        if hasattr(result, "tool_result"):
            result = result.tool_result
        status = "success"
        if isinstance(result, dict) and str(result.get("status", "")).strip().lower() == "error":
            status = "error"
        return status, _summarize_invoke_agent_result(result)

    text = stringify_value(result, max_chars=260)
    lower = text.lower()
    if lower.startswith("error"):
        return "error", text
    if lower.startswith("warning"):
        return "warning", text
    summarizers = {
        "list_dir": _summarize_list_dir_result,
        "glob": _summarize_glob_result,
        "grep": _summarize_grep_result,
        "read_file": _summarize_read_file_result,
        "write_file": _summarize_write_like_result,
        "edit_file": _summarize_write_like_result,
        "image_crop": _summarize_write_like_result,
        "image_rotate": _summarize_write_like_result,
        "image_flip": _summarize_write_like_result,
        "image_info": _summarize_json_metadata_result,
        "image_resize": _summarize_write_like_result,
        "image_convert": _summarize_write_like_result,
        "video_info": _summarize_json_metadata_result,
        "video_extract_frame": _summarize_write_like_result,
        "video_trim": _summarize_write_like_result,
        "video_concat": _summarize_write_like_result,
        "video_convert": _summarize_write_like_result,
        "audio_info": _summarize_json_metadata_result,
        "audio_trim": _summarize_write_like_result,
        "audio_concat": _summarize_write_like_result,
        "audio_convert": _summarize_write_like_result,
        "exec_command": _summarize_exec_result,
        "process_session": _summarize_process_result,
        "web_search": _summarize_web_search_result,
        "web_fetch": _summarize_web_fetch_result,
        "list_session_files": _summarize_list_session_files_result,
    }
    summarizer = summarizers.get(tool_name)
    if summarizer is None:
        return "success", stringify_value(result, max_chars=220)
    return "success", summarizer(str(result))
