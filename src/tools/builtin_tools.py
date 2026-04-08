"""Picobot-style built-in tools for Creative Claw."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from conf.system import SYS_CONFIG


def _default_workspace() -> Path:
    """Return the default workspace root."""
    workspace_env = os.getenv("CREATIVE_CLAW_WORKSPACE")
    return Path(workspace_env).expanduser().resolve() if workspace_env else Path(SYS_CONFIG.base_dir).resolve()


@dataclass(slots=True)
class BuiltinToolbox:
    """Configurable collection of picobot-style built-in tools."""

    workspace_root: Path

    def __init__(self, workspace_root: str | Path | None = None) -> None:
        root = _default_workspace() if workspace_root is None else Path(workspace_root)
        self.workspace_root = root.expanduser().resolve()

    def resolve_path(self, path: str) -> Path:
        """Resolve a user path inside this toolbox workspace root."""
        raw_path = Path(path).expanduser()
        target = raw_path if raw_path.is_absolute() else self.workspace_root / raw_path
        resolved = target.resolve()
        resolved.relative_to(self.workspace_root)
        return resolved

    def read_file(self, path: str) -> str:
        """Read the contents of a UTF-8 text file."""
        try:
            target = self.resolve_path(path)
            if not target.exists():
                return f"Error: File not found: {path}"
            if not target.is_file():
                return f"Error: Not a file: {path}"
            return target.read_text(encoding="utf-8")
        except Exception as exc:
            return f"Error reading file: {exc}"

    def write_file(self, path: str, content: str) -> str:
        """Write UTF-8 text content into a file."""
        try:
            target = self.resolve_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {target.relative_to(self.workspace_root)}"
        except Exception as exc:
            return f"Error writing file: {exc}"

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        """Replace one exact text occurrence in a file."""
        try:
            target = self.resolve_path(path)
            if not target.exists():
                return f"Error: File not found: {path}"
            if not target.is_file():
                return f"Error: Not a file: {path}"

            content = target.read_text(encoding="utf-8")
            count = content.count(old_text)
            if count == 0:
                return "Error: old_text not found in file. Make sure it matches exactly."
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            return f"Successfully edited {target.relative_to(self.workspace_root)}"
        except Exception as exc:
            return f"Error editing file: {exc}"

    def list_dir(self, path: str = ".") -> str:
        """List entries in a directory."""
        try:
            target = self.resolve_path(path)
            if not target.exists():
                return f"Error: Directory not found: {path}"
            if not target.is_dir():
                return f"Error: Not a directory: {path}"

            entries: list[str] = []
            for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                kind = "[D]" if child.is_dir() else "[F]"
                entries.append(f"{kind} {child.relative_to(self.workspace_root)}")
            return "\n".join(entries) if entries else f"Directory {target.relative_to(self.workspace_root)} is empty"
        except Exception as exc:
            return f"Error listing directory: {exc}"

    def exec_command(self, command: str, working_dir: str | None = None, timeout: int = 60) -> str:
        """Execute one shell command and return stdout and stderr."""
        lower = command.strip().lower()
        for pattern in _DENY_PATTERNS:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        try:
            cwd = self.resolve_path(working_dir) if working_dir else self.workspace_root
            completed = subprocess.run(
                command.strip(),
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds"
        except Exception as exc:
            return f"Error executing command: {exc}"

        parts: list[str] = []
        if completed.stdout:
            parts.append(completed.stdout)
        if completed.stderr:
            parts.append(f"STDERR:\n{completed.stderr}")
        if completed.returncode != 0:
            parts.append(f"Exit code: {completed.returncode}")

        result = "\n".join(parts).strip() or "(no output)"
        max_len = 12000
        if len(result) > max_len:
            result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"
        return result

    def web_search(self, query: str, count: int = 5) -> str:
        """Search the web via Brave Search API."""
        api_key = os.getenv("BRAVE_API_KEY", "")
        if not api_key:
            return "Error: BRAVE_API_KEY not configured"

        limit = min(max(count, 1), 10)
        url = f"https://api.search.brave.com/res/v1/web/search?q={query}&count={limit}"
        req = Request(
            url,
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            method="GET",
        )

        try:
            with urlopen(req, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))

            results = payload.get("web", {}).get("results", [])
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}", ""]
            for index, item in enumerate(results[:limit], start=1):
                lines.append(f"{index}. {item.get('title', '')}")
                lines.append(f"   {item.get('url', '')}")
                description = item.get("description", "")
                if description:
                    lines.append(f"   {description}")
            return "\n".join(lines)
        except HTTPError as exc:
            return f"Error: HTTP {exc.code} from Brave Search"
        except URLError as exc:
            return f"Error: Network error: {exc.reason}"
        except Exception as exc:
            return f"Error: {exc}"

    def web_fetch(self, url: str, max_chars: int = 50000) -> str:
        """Fetch one URL and return extracted text as JSON."""
        ok, err = _validate_http_url(url)
        if not ok:
            return _json({"error": err, "url": url})

        req = Request(url, headers={"User-Agent": "creative_claw/0.1"}, method="GET")
        try:
            with urlopen(req, timeout=30) as response:
                status = getattr(response, "status", 200)
                final_url = getattr(response, "url", url)
                content_type = response.headers.get("Content-Type", "")
                raw = response.read()

            text = raw.decode("utf-8", errors="replace")
            if "application/json" in content_type:
                extracted = text
                extractor = "json"
            elif "text/html" in content_type or "<html" in text[:1024].lower():
                no_script = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
                no_style = re.sub(r"<style[\s\S]*?</style>", "", no_script, flags=re.I)
                extracted = re.sub(r"<[^>]+>", "", no_style)
                extracted = re.sub(r"[ \t]+", " ", extracted)
                extracted = re.sub(r"\n{3,}", "\n\n", extracted).strip()
                extractor = "html"
            else:
                extracted = text
                extractor = "raw"

            truncated = len(extracted) > max_chars
            if truncated:
                extracted = extracted[:max_chars]

            return _json(
                {
                    "url": url,
                    "finalUrl": final_url,
                    "status": status,
                    "extractor": extractor,
                    "truncated": truncated,
                    "length": len(extracted),
                    "text": extracted,
                }
            )
        except HTTPError as exc:
            return _json({"error": f"HTTP {exc.code}", "url": url})
        except URLError as exc:
            return _json({"error": f"Network error: {exc.reason}", "url": url})
        except Exception as exc:
            return _json({"error": str(exc), "url": url})


def _get_default_toolbox() -> BuiltinToolbox:
    """Build a default toolbox from the current environment."""
    return BuiltinToolbox()


def _json(obj: Any) -> str:
    """Encode one object as pretty JSON."""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def read_file(path: str) -> str:
    """Read the contents of a UTF-8 text file."""
    return _get_default_toolbox().read_file(path)


def write_file(path: str, content: str) -> str:
    """Write UTF-8 text content into a file."""
    return _get_default_toolbox().write_file(path, content)


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace one exact text occurrence in a file."""
    return _get_default_toolbox().edit_file(path, old_text, new_text)


def list_dir(path: str = ".") -> str:
    """List entries in a directory."""
    return _get_default_toolbox().list_dir(path)


_DENY_PATTERNS = [
    r"\brm\s+-[rf]{1,2}\b",
    r"\bdel\s+/[fq]\b",
    r"\brmdir\s+/s\b",
    r"\b(format|mkfs|diskpart)\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"\b(shutdown|reboot|poweroff)\b",
    r":\(\)\s*\{.*\};\s*:",
]


def exec_command(command: str, working_dir: str | None = None, timeout: int = 60) -> str:
    """Execute one shell command and return stdout and stderr."""
    return _get_default_toolbox().exec_command(command, working_dir=working_dir, timeout=timeout)


def _validate_http_url(url: str) -> tuple[bool, str]:
    """Validate one HTTP or HTTPS URL."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, "Only http/https URLs are supported."
        if not parsed.netloc:
            return False, "URL must include a domain."
        return True, ""
    except Exception as exc:
        return False, str(exc)


def web_search(query: str, count: int = 5) -> str:
    """Search the web via Brave Search API."""
    return _get_default_toolbox().web_search(query, count=count)


def web_fetch(url: str, max_chars: int = 50000) -> str:
    """Fetch one URL and return extracted text as JSON."""
    return _get_default_toolbox().web_fetch(url, max_chars=max_chars)


# Match picobot-style tool naming when shown to the model.
exec_command.__name__ = "exec"
