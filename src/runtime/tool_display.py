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
        preview = f"开头：{head} || 结尾：{tail}"
    else:
        preview = f"开头：{head}"
    return stringify_value(preview, max_chars=max_chars)


def _summarize_list_dir_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("Warning"):
        return result_text
    entries = [line.strip() for line in result_text.splitlines() if line.strip()]
    preview = "; ".join(entries[:3])
    return f"共 {len(entries)} 个条目。预览：{stringify_value(preview, max_chars=180)}"


def _summarize_read_file_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("Warning"):
        return result_text
    char_count = len(result_text)
    line_count = len(result_text.splitlines()) or 1
    preview = head_tail_preview(result_text, max_lines=2, max_chars=220)
    return f"读取成功，约 {char_count} 个字符，{line_count} 行。{preview}"


def _summarize_exec_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("Warning"):
        return result_text
    stdout_text = result_text
    stderr_text = ""
    if "\nSTDERR:\n" in result_text:
        stdout_text, stderr_text = result_text.split("\nSTDERR:\n", 1)
    elif result_text.startswith("STDERR:\n"):
        stdout_text = ""
        stderr_text = result_text[len("STDERR:\n") :]

    stdout_lines = [line for line in stdout_text.splitlines() if line.strip()]
    stderr_lines = [line for line in stderr_text.splitlines() if line.strip()]
    parts = [f"命令执行完成，stdout 约 {len(stdout_lines)} 行"]
    if stdout_lines:
        parts.append(f"stdout 摘要：{head_tail_preview(stdout_text, max_lines=2, max_chars=180)}")
    if stderr_text.strip() or stderr_lines:
        parts.append(f"stderr 约 {len(stderr_lines)} 行")
        parts.append(f"stderr 摘要：{head_tail_preview(stderr_text, max_lines=2, max_chars=180)}")
    return "；".join(parts)


def _summarize_web_search_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("No results") or result_text.startswith("Warning"):
        return result_text
    result_count = sum(1 for line in result_text.splitlines() if re.match(r"^\d+\.\s", line.strip()))
    preview = preview_lines(result_text, max_lines=4, max_chars=180)
    return f"搜索完成，返回 {result_count} 条结果。摘要：{preview}"


def _summarize_web_fetch_result(result_text: str) -> str:
    try:
        payload = json.loads(result_text)
    except Exception:
        return stringify_value(result_text, max_chars=220)

    if not isinstance(payload, dict):
        return stringify_value(result_text, max_chars=220)
    if payload.get("error"):
        return f"抓取失败：{payload.get('error')}"
    text = str(payload.get("text", "")).strip()
    extractor = str(payload.get("extractor", "")).strip() or "unknown"
    length = payload.get("length", len(text))
    preview = head_tail_preview(text, max_lines=2, max_chars=220)
    return f"抓取成功，extractor={extractor}，正文约 {length} 个字符。{preview}"


def _summarize_write_like_result(result_text: str) -> str:
    if result_text.startswith("Error") or result_text.startswith("Warning"):
        return result_text
    return stringify_value(result_text, max_chars=200)


def summarize_tool_result(tool_name: str, result: Any) -> tuple[str, str]:
    """Summarize one tool result into status plus short preview."""
    text = stringify_value(result, max_chars=260)
    lower = text.lower()
    if lower.startswith("error"):
        return "error", text
    if lower.startswith("warning"):
        return "warning", text
    summarizers = {
        "list_dir": _summarize_list_dir_result,
        "read_file": _summarize_read_file_result,
        "write_file": _summarize_write_like_result,
        "edit_file": _summarize_write_like_result,
        "exec_command": _summarize_exec_result,
        "web_search": _summarize_web_search_result,
        "web_fetch": _summarize_web_fetch_result,
    }
    summarizer = summarizers.get(tool_name)
    if summarizer is None:
        return "success", stringify_value(result, max_chars=220)
    return "success", summarizer(str(result))
