"""Picobot-style built-in tools for Creative Claw."""

from __future__ import annotations

import json
import os
import fnmatch
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import ExifTags, Image, ImageOps

from conf.api import API_CONFIG
from src.runtime.process_sessions import get_process_session_manager
from src.runtime.workspace import workspace_root


def _default_workspace() -> Path:
    """Return the default workspace root."""
    return workspace_root()


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

    def _resolve_existing_file(self, path: str) -> Path:
        """Resolve one workspace path and ensure it points to an existing file."""
        source = self.resolve_path(path)
        if not source.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not source.is_file():
            raise IsADirectoryError(f"Not a file: {path}")
        return source

    def _run_subprocess_checked(
        self,
        args: list[str],
        *,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        """Run one subprocess command and raise a readable error on failure."""
        try:
            completed = subprocess.run(
                args,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Required executable not found: {args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Command timed out after {timeout} seconds") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(detail or f"Command failed with exit code {completed.returncode}")
        return completed

    def _probe_media(self, source: Path) -> dict[str, Any]:
        """Run ffprobe for one media file and return parsed JSON metadata."""
        completed = self._run_subprocess_checked(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(source),
            ],
            timeout=60,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ffprobe returned invalid JSON output") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("ffprobe returned an unexpected metadata payload")
        return payload

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

    def glob(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 200,
        entry_type: str = "files",
    ) -> str:
        """Find files or directories matching one glob pattern."""
        try:
            root = self.resolve_path(path)
            if not root.exists():
                return f"Error: Path not found: {path}"
            if not root.is_dir():
                return f"Error: Not a directory: {path}"

            safe_limit = max(1, int(max_results))
            include_files = entry_type in {"files", "both"}
            include_dirs = entry_type in {"dirs", "both"}
            if not include_files and not include_dirs:
                return "Error: entry_type must be one of 'files', 'dirs', or 'both'."

            matches: list[str] = []
            for entry in _iter_entries(root, include_files=include_files, include_dirs=include_dirs):
                rel_path = entry.relative_to(root).as_posix()
                if not _match_glob_pattern(rel_path, entry.name, pattern):
                    continue
                display = rel_path + ("/" if entry.is_dir() else "")
                matches.append(display)

            if not matches:
                return f"No paths matched pattern '{pattern}' in {path}"
            matches.sort()
            result = "\n".join(matches[:safe_limit])
            if len(matches) > safe_limit:
                result += f"\n\n(truncated, showing first {safe_limit} of {len(matches)} matches)"
            return result
        except Exception as exc:
            return f"Error finding files: {exc}"

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
    ) -> str:
        """Search file contents with regex or fixed-string matching."""
        try:
            target = self.resolve_path(path)
            if not target.exists():
                return f"Error: Path not found: {path}"
            if not (target.is_dir() or target.is_file()):
                return f"Error: Unsupported path: {path}"

            flags = re.IGNORECASE if case_insensitive else 0
            needle = re.escape(pattern) if fixed_strings else pattern
            try:
                regex = re.compile(needle, flags)
            except re.error as exc:
                return f"Error: invalid regex pattern: {exc}"

            safe_limit = max(1, int(max_results))
            safe_before = max(0, int(context_before))
            safe_after = max(0, int(context_after))
            blocks: list[str] = []
            counts: dict[str, int] = {}
            matching_files: list[str] = []
            root = target if target.is_dir() else target.parent

            for file_path in _iter_files(target):
                rel_path = file_path.relative_to(root).as_posix()
                if glob_pattern and not _match_glob_pattern(rel_path, file_path.name, glob_pattern):
                    continue
                try:
                    text = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue

                lines = text.splitlines()
                display = rel_path
                file_count = 0
                for line_no, line in enumerate(lines, start=1):
                    if not regex.search(line):
                        continue
                    file_count += 1
                    if output_mode == "files_with_matches":
                        break
                    if output_mode == "count":
                        continue
                    if len(blocks) >= safe_limit:
                        break
                    start = max(1, line_no - safe_before)
                    end = min(len(lines), line_no + safe_after)
                    block_lines = [f"{display}:{line_no}"]
                    for current in range(start, end + 1):
                        marker = ">" if current == line_no else " "
                        block_lines.append(f"{marker} {current}| {lines[current - 1]}")
                    blocks.append("\n".join(block_lines))
                if file_count == 0:
                    continue
                matching_files.append(display)
                counts[display] = file_count
                if output_mode == "content" and len(blocks) >= safe_limit:
                    break

            if output_mode == "files_with_matches":
                if not matching_files:
                    return f"No matches found for pattern '{pattern}' in {path}"
                ordered = sorted(matching_files)
                result = "\n".join(ordered[:safe_limit])
                if len(ordered) > safe_limit:
                    result += f"\n\n(truncated, showing first {safe_limit} of {len(ordered)} matching files)"
                return result

            if output_mode == "count":
                if not counts:
                    return f"No matches found for pattern '{pattern}' in {path}"
                ordered = sorted(counts.items())
                result = "\n".join(f"{name}: {count}" for name, count in ordered[:safe_limit])
                if len(ordered) > safe_limit:
                    result += f"\n\n(truncated, showing first {safe_limit} of {len(ordered)} matching files)"
                return result

            if output_mode != "content":
                return "Error: output_mode must be one of 'files_with_matches', 'count', or 'content'."
            if not blocks:
                return f"No matches found for pattern '{pattern}' in {path}"
            result = "\n\n".join(blocks[:safe_limit])
            if len(blocks) > safe_limit:
                result += f"\n\n(truncated, showing first {safe_limit} matches)"
            return result
        except Exception as exc:
            return f"Error searching files: {exc}"

    def image_crop(self, path: str, left: int, top: int, right: int, bottom: int) -> str:
        """Crop an image and save the result next to the input file."""
        try:
            source = self._resolve_existing_file(path)
            if right <= left or bottom <= top:
                return "Error: Invalid crop box. Ensure right > left and bottom > top."

            with Image.open(source) as image:
                cropped = image.crop((left, top, right, bottom))
                destination = _derived_image_output_path(source, "crop")
                cropped.save(destination)

            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error cropping image: {exc}"

    def image_rotate(self, path: str, degrees: float, expand: bool = True) -> str:
        """Rotate an image and save the result next to the input file."""
        try:
            source = self._resolve_existing_file(path)

            with Image.open(source) as image:
                rotated = image.rotate(degrees, expand=expand)
                suffix = f"rotate_{_format_rotation_suffix(degrees)}"
                destination = _derived_image_output_path(source, suffix)
                rotated.save(destination)

            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error rotating image: {exc}"

    def image_flip(self, path: str, direction: str) -> str:
        """Flip an image horizontally or vertically and save the result."""
        try:
            source = self._resolve_existing_file(path)

            normalized = direction.strip().lower()
            with Image.open(source) as image:
                if normalized == "horizontal":
                    flipped = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                elif normalized == "vertical":
                    flipped = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                else:
                    return "Error: direction must be 'horizontal' or 'vertical'."

                destination = _derived_image_output_path(source, f"flip_{normalized}")
                flipped.save(destination)

            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error flipping image: {exc}"

    def image_info(self, path: str) -> str:
        """Read deterministic metadata from one workspace image file."""
        try:
            source = self._resolve_existing_file(path)
            with Image.open(source) as image:
                exif_payload: dict[str, Any] = {}
                raw_exif = image.getexif()
                if raw_exif:
                    for tag_id, value in raw_exif.items():
                        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                        exif_payload[str(tag_name)] = _normalize_metadata_value(value)

                return _json(
                    {
                        "path": str(source.relative_to(self.workspace_root)),
                        "format": image.format or "unknown",
                        "width": image.width,
                        "height": image.height,
                        "mode": image.mode,
                        "exif": exif_payload,
                    }
                )
        except Exception as exc:
            return f"Error reading image info: {exc}"

    def image_resize(
        self,
        path: str,
        width: int | None = None,
        height: int | None = None,
        keep_aspect_ratio: bool = True,
        resample: str = "lanczos",
    ) -> str:
        """Resize one image to a target size and save the result next to the source."""
        try:
            source = self._resolve_existing_file(path)
            target_width = _normalize_positive_int(width, "width") if width is not None else None
            target_height = _normalize_positive_int(height, "height") if height is not None else None
            if target_width is None and target_height is None:
                return "Error: image_resize requires width, height, or both."

            resample_filter = _normalize_image_resample(resample)
            with Image.open(source) as image:
                final_width, final_height = _resolve_resize_dimensions(
                    image.width,
                    image.height,
                    target_width,
                    target_height,
                    keep_aspect_ratio=keep_aspect_ratio,
                )
                if keep_aspect_ratio and target_width is not None and target_height is not None:
                    resized = ImageOps.contain(
                        image,
                        (target_width, target_height),
                        method=resample_filter,
                    )
                    final_width, final_height = resized.size
                else:
                    resized = image.resize((final_width, final_height), resample=resample_filter)

                destination = _derived_image_output_path(
                    source,
                    f"resize_{final_width}x{final_height}",
                )
                resized.save(destination)

            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error resizing image: {exc}"

    def image_convert(
        self,
        path: str,
        output_format: str,
        mode: str | None = None,
        quality: int | None = None,
    ) -> str:
        """Convert one image to another format and save the converted file."""
        try:
            source = self._resolve_existing_file(path)
            normalized_format, extension = _normalize_image_output_format(output_format)
            normalized_quality = (
                _normalize_bounded_int(quality, "quality", minimum=1, maximum=100)
                if quality is not None
                else None
            )

            with Image.open(source) as image:
                converted = image.copy()
                if mode:
                    converted = converted.convert(str(mode).strip())
                elif normalized_format == "JPEG" and converted.mode not in {"RGB", "L"}:
                    converted = converted.convert("RGB")

                destination = _derived_output_path(source, "convert", extension=extension)
                save_kwargs: dict[str, Any] = {"format": normalized_format}
                if normalized_quality is not None and normalized_format in {"JPEG", "WEBP"}:
                    save_kwargs["quality"] = normalized_quality
                converted.save(destination, **save_kwargs)

            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error converting image: {exc}"

    def video_info(self, path: str) -> str:
        """Read deterministic metadata from one workspace video file."""
        try:
            source = self._resolve_existing_file(path)
            payload = self._probe_media(source)
            video_stream = _find_stream(payload, "video")
            if video_stream is None:
                return f"Error reading video info: No video stream found in {path}"
            audio_stream = _find_stream(payload, "audio")
            format_info = payload.get("format", {})
            return _json(
                {
                    "path": str(source.relative_to(self.workspace_root)),
                    "duration_seconds": _extract_duration_seconds(payload, preferred_stream=video_stream),
                    "width": _safe_int(video_stream.get("width")),
                    "height": _safe_int(video_stream.get("height")),
                    "fps": _parse_frame_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
                    "video_codec": str(video_stream.get("codec_name", "")).strip() or None,
                    "audio_codec": str(audio_stream.get("codec_name", "")).strip() or None,
                    "container": str(format_info.get("format_name", "")).strip() or None,
                    "bit_rate": _safe_int(format_info.get("bit_rate")),
                }
            )
        except Exception as exc:
            return f"Error reading video info: {exc}"

    def video_extract_frame(
        self,
        path: str,
        timestamp: str,
        output_format: str = "png",
    ) -> str:
        """Extract one video frame at the requested timestamp."""
        try:
            source = self._resolve_existing_file(path)
            normalized_timestamp = str(timestamp).strip()
            if not normalized_timestamp:
                return "Error: video_extract_frame requires a non-empty timestamp."
            _image_format, extension = _normalize_image_output_format(output_format)
            destination = _derived_output_path(
                source,
                f"frame_{_sanitize_suffix(normalized_timestamp)}",
                extension=extension,
            )
            self._run_subprocess_checked(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    normalized_timestamp,
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    str(destination),
                ],
                timeout=120,
            )
            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error extracting video frame: {exc}"

    def video_trim(
        self,
        path: str,
        start_time: str,
        end_time: str | None = None,
        duration: str | None = None,
    ) -> str:
        """Trim one video clip by time range and save the trimmed output."""
        try:
            source = self._resolve_existing_file(path)
            normalized_start = str(start_time).strip()
            normalized_end = str(end_time).strip() if end_time is not None else ""
            normalized_duration = str(duration).strip() if duration is not None else ""
            if not normalized_start:
                return "Error: video_trim requires start_time."
            if bool(normalized_end) == bool(normalized_duration):
                return "Error: video_trim requires exactly one of end_time or duration."

            destination = _derived_output_path(source, "trim")
            command = [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-ss",
                normalized_start,
            ]
            if normalized_end:
                command.extend(["-to", normalized_end])
            else:
                command.extend(["-t", normalized_duration])
            command.extend(["-c", "copy", str(destination)])
            self._run_subprocess_checked(command, timeout=180)
            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error trimming video: {exc}"

    def video_concat(self, paths: list[str], output_format: str | None = None) -> str:
        """Concatenate compatible video clips in order and save the merged result."""
        try:
            if len(paths) < 2:
                return "Error: video_concat requires at least two input paths."
            sources = [self._resolve_existing_file(path) for path in paths]
            extension = _resolve_video_extension(output_format, fallback=sources[0].suffix or ".mp4")
            destination = _derived_output_path(sources[0], "concat", extension=extension)
            concat_file = self._write_concat_manifest(sources)
            try:
                self._run_subprocess_checked(
                    [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat_file),
                        "-c",
                        "copy",
                        str(destination),
                    ],
                    timeout=240,
                )
            finally:
                concat_file.unlink(missing_ok=True)
            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error concatenating video: {exc}"

    def video_convert(
        self,
        path: str,
        output_format: str,
        video_codec: str | None = None,
        audio_codec: str | None = None,
    ) -> str:
        """Convert one video file to another container or codec combination."""
        try:
            source = self._resolve_existing_file(path)
            payload = self._probe_media(source)
            extension = _resolve_video_extension(output_format)
            normalized_format = extension.lstrip(".")
            destination = _derived_output_path(source, "convert", extension=extension)
            command = ["ffmpeg", "-y", "-i", str(source)]
            normalized_video_codec = (
                str(video_codec).strip() if video_codec is not None else ""
            ) or _default_video_codec(normalized_format)
            command.extend(["-c:v", normalized_video_codec])
            if _find_stream(payload, "audio") is not None:
                normalized_audio_codec = (
                    str(audio_codec).strip() if audio_codec is not None else ""
                ) or _default_video_audio_codec(normalized_format)
                command.extend(["-c:a", normalized_audio_codec])
            command.append(str(destination))
            self._run_subprocess_checked(command, timeout=240)
            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error converting video: {exc}"

    def audio_info(self, path: str) -> str:
        """Read deterministic metadata from one workspace audio file."""
        try:
            source = self._resolve_existing_file(path)
            payload = self._probe_media(source)
            audio_stream = _find_stream(payload, "audio")
            if audio_stream is None:
                return f"Error reading audio info: No audio stream found in {path}"
            format_info = payload.get("format", {})
            return _json(
                {
                    "path": str(source.relative_to(self.workspace_root)),
                    "duration_seconds": _extract_duration_seconds(payload, preferred_stream=audio_stream),
                    "sample_rate": _safe_int(audio_stream.get("sample_rate")),
                    "channels": _safe_int(audio_stream.get("channels")),
                    "codec": str(audio_stream.get("codec_name", "")).strip() or None,
                    "bit_rate": _safe_int(audio_stream.get("bit_rate") or format_info.get("bit_rate")),
                    "container": str(format_info.get("format_name", "")).strip() or None,
                }
            )
        except Exception as exc:
            return f"Error reading audio info: {exc}"

    def audio_trim(
        self,
        path: str,
        start_time: str,
        end_time: str | None = None,
        duration: str | None = None,
    ) -> str:
        """Trim one audio clip by time range and save the trimmed result."""
        try:
            source = self._resolve_existing_file(path)
            normalized_start = str(start_time).strip()
            normalized_end = str(end_time).strip() if end_time is not None else ""
            normalized_duration = str(duration).strip() if duration is not None else ""
            if not normalized_start:
                return "Error: audio_trim requires start_time."
            if bool(normalized_end) == bool(normalized_duration):
                return "Error: audio_trim requires exactly one of end_time or duration."

            destination = _derived_output_path(source, "trim")
            command = [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-ss",
                normalized_start,
            ]
            if normalized_end:
                command.extend(["-to", normalized_end])
            else:
                command.extend(["-t", normalized_duration])
            command.extend(["-c", "copy", str(destination)])
            self._run_subprocess_checked(command, timeout=180)
            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error trimming audio: {exc}"

    def audio_concat(self, paths: list[str], output_format: str | None = None) -> str:
        """Concatenate compatible audio clips in order and save the merged output."""
        try:
            if len(paths) < 2:
                return "Error: audio_concat requires at least two input paths."
            sources = [self._resolve_existing_file(path) for path in paths]
            extension = _resolve_audio_extension(output_format, fallback=sources[0].suffix or ".wav")
            destination = _derived_output_path(sources[0], "concat", extension=extension)
            concat_file = self._write_concat_manifest(sources)
            try:
                self._run_subprocess_checked(
                    [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat_file),
                        "-c",
                        "copy",
                        str(destination),
                    ],
                    timeout=240,
                )
            finally:
                concat_file.unlink(missing_ok=True)
            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error concatenating audio: {exc}"

    def audio_convert(
        self,
        path: str,
        output_format: str,
        sample_rate: int | None = None,
        bitrate: str | None = None,
        channels: int | None = None,
    ) -> str:
        """Convert one audio file to another format with optional encoding settings."""
        try:
            source = self._resolve_existing_file(path)
            extension = _resolve_audio_extension(output_format)
            normalized_format = extension.lstrip(".")
            destination = _derived_output_path(source, "convert", extension=extension)
            command = [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-c:a",
                _default_audio_codec(normalized_format),
            ]
            if sample_rate is not None:
                command.extend(["-ar", str(_normalize_positive_int(sample_rate, "sample_rate"))])
            if channels is not None:
                command.extend(["-ac", str(_normalize_positive_int(channels, "channels"))])
            if bitrate is not None:
                normalized_bitrate = str(bitrate).strip()
                if not normalized_bitrate:
                    return "Error: bitrate must be a non-empty string when provided."
                command.extend(["-b:a", normalized_bitrate])
            command.append(str(destination))
            self._run_subprocess_checked(command, timeout=240)
            return str(destination.relative_to(self.workspace_root))
        except Exception as exc:
            return f"Error converting audio: {exc}"

    def _write_concat_manifest(self, sources: list[Path]) -> Path:
        """Write a temporary ffmpeg concat manifest inside the workspace."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="ffmpeg_concat_",
            dir=self.workspace_root,
            delete=False,
        ) as handle:
            for source in sources:
                handle.write(f"file '{_escape_concat_path(source)}'\n")
            return Path(handle.name)

    def exec_command(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int = 60,
        background: bool = False,
        yield_ms: int = 1000,
        scope_key: str | None = None,
    ) -> str:
        """Execute one shell command and return stdout and stderr."""
        lower = command.strip().lower()
        for pattern in _DENY_PATTERNS:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        try:
            cwd = self.resolve_path(working_dir) if working_dir else self.workspace_root
            if background:
                manager = get_process_session_manager()
                session = manager.start_session(command=command.strip(), cwd=cwd, scope_key=scope_key)
                initial = manager.poll_session(
                    session.session_id,
                    timeout_ms=max(0, int(yield_ms)),
                    scope_key=scope_key,
                )
                if initial and bool(initial.get("exited")):
                    output = str(initial.get("output", "")).strip()
                    if not output:
                        output = "(no output)"
                    exit_code = initial.get("exit_code")
                    if isinstance(exit_code, int) and exit_code != 0:
                        output = f"{output}\nExit code: {exit_code}".strip()
                    manager.remove_session(session.session_id, scope_key=scope_key)
                    return output
                return (
                    f"Command still running (session {session.session_id}, pid {session.process.pid or 'n/a'}). "
                    "Use process_session(action='list'|'poll'|'log'|'write'|'kill'|'remove') for follow-up."
                )

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

    def process_session(
        self,
        action: str = "list",
        session_id: str | None = None,
        input_text: str = "",
        timeout_ms: int = 0,
        offset: int = 0,
        limit: int = 200,
        scope_key: str | None = None,
    ) -> str:
        """Manage background sessions started by `exec_command`."""
        manager = get_process_session_manager()
        normalized = action.strip().lower()

        if normalized == "list":
            sessions = manager.list_sessions(scope_key=scope_key)
            if not sessions:
                return "No running or recent sessions."
            now = time.time()
            lines = []
            for item in sessions:
                runtime = max(0, int(now - item.started_at))
                label = item.command.replace("\n", " ").strip()
                if len(label) > 100:
                    label = label[:100].rstrip() + "..."
                lines.append(
                    f"{item.session_id} {item.status:7} {runtime:>4}s pid={item.pid or 'n/a'} :: {label}"
                )
            return "\n".join(lines)

        if not session_id:
            return "Error: session_id is required for this action."

        sid = session_id.strip()
        if normalized == "poll":
            payload = manager.poll_session(sid, timeout_ms=max(0, int(timeout_ms)), scope_key=scope_key)
            if payload is None:
                return f"Error: No session found for {sid}"
            output = str(payload.get("output", "")).strip() or "(no new output)"
            status = str(payload.get("status", "running"))
            exit_code = payload.get("exit_code")
            suffix = f"Status: {status}"
            if isinstance(exit_code, int):
                suffix += f"\nExit code: {exit_code}"
            return f"{output}\n\n{suffix}".strip()

        if normalized == "log":
            payload = manager.get_log(
                sid,
                offset=max(0, int(offset)),
                limit=max(1, int(limit)),
                scope_key=scope_key,
            )
            if payload is None:
                return f"Error: No session found for {sid}"
            lines = payload.get("lines") or []
            body = "\n".join(lines) if lines else "(no output yet)"
            if payload.get("has_more"):
                body += f"\n\n(truncated, read from offset {payload['offset'] + payload['limit']})"
            return body

        if normalized == "write":
            if manager.write_session(sid, input_text, scope_key=scope_key):
                return f"Sent {len(input_text)} characters to session {sid}."
            return f"Error: Failed to write to session {sid}"

        if normalized == "kill":
            if manager.kill_session(sid, scope_key=scope_key):
                return f"Kill signal sent to session {sid}."
            return f"Error: Failed to kill session {sid}"

        if normalized == "remove":
            if manager.remove_session(sid, scope_key=scope_key):
                return f"Removed session {sid}."
            return f"Error: Failed to remove session {sid}. The session may still be running."

        return "Error: action must be one of 'list', 'poll', 'log', 'write', 'kill', or 'remove'."

    def web_search(self, query: str, count: int = 5) -> str:
        """Search the web via Brave Search API."""
        api_key = API_CONFIG.BRAVE_API_KEY
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


_IMAGE_RESAMPLE_FILTERS = {
    "nearest": Image.Resampling.NEAREST,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
}

_IMAGE_OUTPUT_FORMATS = {
    "png": ("PNG", ".png"),
    "jpg": ("JPEG", ".jpg"),
    "jpeg": ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
}

_VIDEO_OUTPUT_EXTENSIONS = {
    "mp4": ".mp4",
    "mov": ".mov",
    "mkv": ".mkv",
    "webm": ".webm",
}

_AUDIO_OUTPUT_EXTENSIONS = {
    "mp3": ".mp3",
    "wav": ".wav",
    "aac": ".aac",
    "m4a": ".m4a",
    "flac": ".flac",
    "ogg": ".ogg",
}


def _normalize_positive_int(value: Any, name: str) -> int:
    """Normalize one positive integer parameter."""
    normalized = int(value)
    if normalized <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return normalized


def _normalize_bounded_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    """Normalize one bounded integer parameter."""
    normalized = int(value)
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return normalized


def _normalize_image_resample(value: str) -> Image.Resampling:
    """Resolve one image resampling name into a Pillow resampling filter."""
    normalized = str(value).strip().lower() or "lanczos"
    if normalized not in _IMAGE_RESAMPLE_FILTERS:
        raise ValueError(
            f"Unsupported image resample mode: {value}. "
            f"Allowed values: {sorted(_IMAGE_RESAMPLE_FILTERS)}"
        )
    return _IMAGE_RESAMPLE_FILTERS[normalized]


def _normalize_image_output_format(value: str) -> tuple[str, str]:
    """Resolve one image output format into Pillow format and file extension."""
    normalized = str(value).strip().lower().lstrip(".")
    if normalized not in _IMAGE_OUTPUT_FORMATS:
        raise ValueError(
            f"Unsupported image output format: {value}. "
            f"Allowed values: {sorted(_IMAGE_OUTPUT_FORMATS)}"
        )
    return _IMAGE_OUTPUT_FORMATS[normalized]


def _resolve_video_extension(value: str | None, *, fallback: str | None = None) -> str:
    """Resolve one video output format into a file extension."""
    if value is None:
        if fallback:
            return fallback if fallback.startswith(".") else f".{fallback}"
        return ".mp4"
    normalized = str(value).strip().lower().lstrip(".")
    if normalized not in _VIDEO_OUTPUT_EXTENSIONS:
        raise ValueError(
            f"Unsupported video output format: {value}. "
            f"Allowed values: {sorted(_VIDEO_OUTPUT_EXTENSIONS)}"
        )
    return _VIDEO_OUTPUT_EXTENSIONS[normalized]


def _resolve_audio_extension(value: str | None, *, fallback: str | None = None) -> str:
    """Resolve one audio output format into a file extension."""
    if value is None:
        if fallback:
            return fallback if fallback.startswith(".") else f".{fallback}"
        return ".wav"
    normalized = str(value).strip().lower().lstrip(".")
    if normalized not in _AUDIO_OUTPUT_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio output format: {value}. "
            f"Allowed values: {sorted(_AUDIO_OUTPUT_EXTENSIONS)}"
        )
    return _AUDIO_OUTPUT_EXTENSIONS[normalized]


def _derived_image_output_path(source: Path, suffix: str) -> Path:
    """Build one deterministic output path next to the input image."""
    extension = source.suffix or ".png"
    return source.with_name(f"{source.stem}_{suffix}{extension}")


def _derived_output_path(source: Path, suffix: str, extension: str | None = None) -> Path:
    """Build one deterministic output path next to an existing source file."""
    resolved_extension = extension or source.suffix or ".bin"
    return source.with_name(f"{source.stem}_{suffix}{resolved_extension}")


def _format_rotation_suffix(degrees: float) -> str:
    """Convert rotation degrees into a filename-safe suffix."""
    integer_value = int(degrees)
    return str(integer_value) if integer_value == degrees else str(degrees).replace(".", "_")


def _normalize_metadata_value(value: Any) -> Any:
    """Convert metadata values into JSON-serializable primitives."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _normalize_metadata_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_metadata_value(item) for item in value]
    return str(value)


def _resolve_resize_dimensions(
    source_width: int,
    source_height: int,
    target_width: int | None,
    target_height: int | None,
    *,
    keep_aspect_ratio: bool,
) -> tuple[int, int]:
    """Resolve the final image size for resize operations."""
    if target_width is None and target_height is None:
        raise ValueError("width or height is required")
    if target_width is None:
        ratio = target_height / source_height
        return max(1, round(source_width * ratio)), target_height
    if target_height is None:
        ratio = target_width / source_width
        return target_width, max(1, round(source_height * ratio))
    if not keep_aspect_ratio:
        return target_width, target_height

    scale = min(target_width / source_width, target_height / source_height)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def _sanitize_suffix(value: str) -> str:
    """Convert arbitrary text into a filename-safe suffix component."""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return normalized.strip("._-") or "value"


def _escape_concat_path(path: Path) -> str:
    """Escape one filesystem path for an ffmpeg concat manifest."""
    return str(path).replace("\\", "\\\\").replace("'", r"'\''")


def _find_stream(payload: dict[str, Any], codec_type: str) -> dict[str, Any] | None:
    """Return the first stream matching the requested codec type."""
    streams = payload.get("streams", [])
    for stream in streams:
        if isinstance(stream, dict) and str(stream.get("codec_type", "")).strip() == codec_type:
            return stream
    return None


def _safe_int(value: Any) -> int | None:
    """Convert one optional numeric value to int when possible."""
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    """Convert one optional numeric value to float when possible."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_duration_seconds(
    payload: dict[str, Any],
    *,
    preferred_stream: dict[str, Any] | None = None,
) -> float | None:
    """Extract duration in seconds from ffprobe payload."""
    for candidate in (
        preferred_stream.get("duration") if preferred_stream else None,
        payload.get("format", {}).get("duration"),
    ):
        duration = _safe_float(candidate)
        if duration is not None:
            return duration
    return None


def _parse_frame_rate(value: Any) -> float | None:
    """Parse one ffprobe frame-rate value such as `30000/1001`."""
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    if "/" in text:
        try:
            return round(float(Fraction(text)), 3)
        except (ZeroDivisionError, ValueError):
            return None
    return _safe_float(text)


def _default_video_codec(output_format: str) -> str:
    """Return the default video codec for a target output container."""
    return {
        "mp4": "libx264",
        "mov": "libx264",
        "mkv": "libx264",
        "webm": "libvpx-vp9",
    }.get(output_format, "libx264")


def _default_video_audio_codec(output_format: str) -> str:
    """Return the default audio codec used for video container conversion."""
    return {
        "mp4": "aac",
        "mov": "aac",
        "mkv": "aac",
        "webm": "libopus",
    }.get(output_format, "aac")


def _default_audio_codec(output_format: str) -> str:
    """Return the default audio codec for a target output format."""
    return {
        "mp3": "libmp3lame",
        "wav": "pcm_s16le",
        "aac": "aac",
        "m4a": "aac",
        "flac": "flac",
        "ogg": "libvorbis",
    }.get(output_format, "aac")


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


def glob(pattern: str, path: str = ".", max_results: int = 200, entry_type: str = "files") -> str:
    """Find files or directories matching one glob pattern."""
    return _get_default_toolbox().glob(pattern, path=path, max_results=max_results, entry_type=entry_type)


def grep(
    pattern: str,
    path: str = ".",
    glob_pattern: str | None = None,
    case_insensitive: bool = False,
    fixed_strings: bool = False,
    output_mode: str = "files_with_matches",
    context_before: int = 0,
    context_after: int = 0,
    max_results: int = 100,
) -> str:
    """Search file contents with regex or fixed-string matching."""
    return _get_default_toolbox().grep(
        pattern,
        path=path,
        glob_pattern=glob_pattern,
        case_insensitive=case_insensitive,
        fixed_strings=fixed_strings,
        output_mode=output_mode,
        context_before=context_before,
        context_after=context_after,
        max_results=max_results,
    )


def image_crop(path: str, left: int, top: int, right: int, bottom: int) -> str:
    """Crop an image and return the saved output path."""
    return _get_default_toolbox().image_crop(path, left, top, right, bottom)


def image_rotate(path: str, degrees: float, expand: bool = True) -> str:
    """Rotate an image and return the saved output path."""
    return _get_default_toolbox().image_rotate(path, degrees, expand=expand)


def image_flip(path: str, direction: str) -> str:
    """Flip an image and return the saved output path."""
    return _get_default_toolbox().image_flip(path, direction)


def image_info(path: str) -> str:
    """Read deterministic metadata from one image file."""
    return _get_default_toolbox().image_info(path)


def image_resize(
    path: str,
    width: int | None = None,
    height: int | None = None,
    keep_aspect_ratio: bool = True,
    resample: str = "lanczos",
) -> str:
    """Resize one image and return the saved output path."""
    return _get_default_toolbox().image_resize(
        path,
        width=width,
        height=height,
        keep_aspect_ratio=keep_aspect_ratio,
        resample=resample,
    )


def image_convert(
    path: str,
    output_format: str,
    mode: str | None = None,
    quality: int | None = None,
) -> str:
    """Convert one image and return the saved output path."""
    return _get_default_toolbox().image_convert(
        path,
        output_format=output_format,
        mode=mode,
        quality=quality,
    )


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


def exec_command(
    command: str,
    working_dir: str | None = None,
    timeout: int = 60,
    background: bool = False,
    yield_ms: int = 1000,
    scope_key: str | None = None,
) -> str:
    """Execute one shell command and return stdout and stderr."""
    return _get_default_toolbox().exec_command(
        command,
        working_dir=working_dir,
        timeout=timeout,
        background=background,
        yield_ms=yield_ms,
        scope_key=scope_key,
    )


def process_session(
    action: str = "list",
    session_id: str | None = None,
    input_text: str = "",
    timeout_ms: int = 0,
    offset: int = 0,
    limit: int = 200,
    scope_key: str | None = None,
) -> str:
    """Manage background sessions started by `exec_command`."""
    return _get_default_toolbox().process_session(
        action=action,
        session_id=session_id,
        input_text=input_text,
        timeout_ms=timeout_ms,
        offset=offset,
        limit=limit,
        scope_key=scope_key,
    )


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


def video_info(path: str) -> str:
    """Read deterministic metadata from one video file."""
    return _get_default_toolbox().video_info(path)


def video_extract_frame(path: str, timestamp: str, output_format: str = "png") -> str:
    """Extract one frame from one video file."""
    return _get_default_toolbox().video_extract_frame(
        path,
        timestamp=timestamp,
        output_format=output_format,
    )


def video_trim(
    path: str,
    start_time: str,
    end_time: str | None = None,
    duration: str | None = None,
) -> str:
    """Trim one video file and return the saved output path."""
    return _get_default_toolbox().video_trim(
        path,
        start_time=start_time,
        end_time=end_time,
        duration=duration,
    )


def video_concat(paths: list[str], output_format: str | None = None) -> str:
    """Concatenate video files and return the saved output path."""
    return _get_default_toolbox().video_concat(paths, output_format=output_format)


def video_convert(
    path: str,
    output_format: str,
    video_codec: str | None = None,
    audio_codec: str | None = None,
) -> str:
    """Convert one video file and return the saved output path."""
    return _get_default_toolbox().video_convert(
        path,
        output_format=output_format,
        video_codec=video_codec,
        audio_codec=audio_codec,
    )


def audio_info(path: str) -> str:
    """Read deterministic metadata from one audio file."""
    return _get_default_toolbox().audio_info(path)


def audio_trim(
    path: str,
    start_time: str,
    end_time: str | None = None,
    duration: str | None = None,
) -> str:
    """Trim one audio file and return the saved output path."""
    return _get_default_toolbox().audio_trim(
        path,
        start_time=start_time,
        end_time=end_time,
        duration=duration,
    )


def audio_concat(paths: list[str], output_format: str | None = None) -> str:
    """Concatenate audio files and return the saved output path."""
    return _get_default_toolbox().audio_concat(paths, output_format=output_format)


def audio_convert(
    path: str,
    output_format: str,
    sample_rate: int | None = None,
    bitrate: str | None = None,
    channels: int | None = None,
) -> str:
    """Convert one audio file and return the saved output path."""
    return _get_default_toolbox().audio_convert(
        path,
        output_format=output_format,
        sample_rate=sample_rate,
        bitrate=bitrate,
        channels=channels,
    )


# Match picobot-style tool naming when shown to the model.
exec_command.__name__ = "exec"
process_session.__name__ = "process"


_IGNORE_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
}


def _iter_entries(root: Path, *, include_files: bool, include_dirs: bool):
    """Yield workspace entries while skipping noisy directories."""
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(name for name in dir_names if name not in _IGNORE_DIR_NAMES)
        current = Path(current_root)
        if include_dirs and current != root:
            yield current
        if include_files:
            for file_name in sorted(file_names):
                yield current / file_name


def _iter_files(target: Path):
    """Yield text-like files below one path."""
    if target.is_file():
        yield target
        return
    for entry in _iter_entries(target, include_files=True, include_dirs=False):
        if entry.is_file():
            yield entry


def _match_glob_pattern(relative_path: str, entry_name: str, pattern: str) -> bool:
    """Match one pattern against both relative path and basename."""
    if "/" in pattern or "**" in pattern:
        return fnmatch.fnmatch(relative_path, pattern)
    return fnmatch.fnmatch(entry_name, pattern) or fnmatch.fnmatch(relative_path, pattern)
