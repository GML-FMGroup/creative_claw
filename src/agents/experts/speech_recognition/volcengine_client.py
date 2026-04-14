"""Volcengine speech-service client helpers for speech recognition flows."""

from __future__ import annotations

import base64
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any

import requests

from conf.api import API_CONFIG

_BIGASR_FLASH_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
_SUBTITLE_BASE_URL = "https://openspeech.bytedance.com/api/v1/vc"
_SUBTITLE_SUBMIT_URL = f"{_SUBTITLE_BASE_URL}/submit"
_SUBTITLE_QUERY_URL = f"{_SUBTITLE_BASE_URL}/query"
_SUBTITLE_ALIGN_SUBMIT_URL = f"{_SUBTITLE_BASE_URL}/ata/submit"
_SUBTITLE_ALIGN_QUERY_URL = f"{_SUBTITLE_BASE_URL}/ata/query"
_BIGASR_RESOURCE_ID = "volc.bigasr.auc_turbo"
_SUBTITLE_GENERATION_RESOURCE_ID = "vc.async.default"
_SUBTITLE_ALIGNMENT_RESOURCE_ID = "volc.ata.default"
_RESOURCE_PERMISSION_MARKERS = ("requested resource not granted", "requested grant not found")
_RESOURCE_ID_RE = re.compile(r"resource_id=([A-Za-z0-9._-]+)")


@dataclass(frozen=True, slots=True)
class VolcengineSpeechCredentials:
    """Credentials required by the Volcengine speech APIs used in Creative Claw."""

    app_id: str
    access_token: str


def load_volcengine_speech_credentials() -> VolcengineSpeechCredentials:
    """Load the configured Volcengine speech credentials from env or runtime config."""
    app_id = os.environ.get("VOLCENGINE_APPID", "").strip() or str(API_CONFIG.VOLCENGINE_APPID).strip()
    access_token = os.environ.get("VOLCENGINE_ACCESS_TOKEN", "").strip() or str(API_CONFIG.VOLCENGINE_ACCESS_TOKEN).strip()
    if not app_id or not access_token:
        raise RuntimeError(
            "Volcengine speech credentials are not configured. Required: VOLCENGINE_APPID and VOLCENGINE_ACCESS_TOKEN."
        )
    return VolcengineSpeechCredentials(app_id=app_id, access_token=access_token)


class VolcengineSpeechClient:
    """Small requests-based client for the Volcengine speech services used here."""

    def __init__(
        self,
        credentials: VolcengineSpeechCredentials | None = None,
        *,
        session: requests.Session | None = None,
    ) -> None:
        """Initialize the client with runtime credentials and one HTTP session."""
        self.credentials = credentials or load_volcengine_speech_credentials()
        self.session = session or requests.Session()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    def recognize_flash(
        self,
        *,
        user_id: str,
        media_bytes: bytes,
        language: str = "",
        enable_itn: bool = True,
        enable_punc: bool = True,
        enable_ddc: bool = False,
        enable_speaker_info: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Call Volcengine big-ASR flash recognition with one local media payload."""
        current_request_id = request_id or str(uuid.uuid4())
        headers = {
            "X-Api-App-Key": self.credentials.app_id,
            "X-Api-Access-Key": self.credentials.access_token,
            "X-Api-Resource-Id": _BIGASR_RESOURCE_ID,
            "X-Api-Request-Id": current_request_id,
            "X-Api-Sequence": "-1",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "user": {"uid": user_id or self.credentials.app_id},
            "audio": {
                "data": base64.b64encode(media_bytes).decode("utf-8"),
                "format": "wav",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "show_utterances": True,
                "enable_itn": enable_itn,
                "enable_punc": enable_punc,
                "enable_ddc": enable_ddc,
                "enable_speaker_info": enable_speaker_info,
            },
        }
        normalized_language = str(language).strip()
        if normalized_language:
            payload["audio"]["language"] = normalized_language

        response = self.session.post(
            _BIGASR_FLASH_URL,
            headers=headers,
            json=payload,
            timeout=(30, 600),
        )
        body = _decode_json_response(response)
        status_code = str(response.headers.get("X-Api-Status-Code", "")).strip()
        if response.status_code >= 400 or (status_code and status_code != "20000000"):
            raise RuntimeError(
                _build_volcengine_error(
                    "Volcengine big-ASR flash",
                    body=body,
                    http_status=response.status_code,
                    api_status=status_code or "<missing>",
                    expected_resource_id=_BIGASR_RESOURCE_ID,
                    capability_name="SpeechRecognitionExpert ASR (`task=asr`)",
                )
            )

        result = body.get("result", {}) if isinstance(body, dict) else {}
        return {
            "provider": "volcengine_bigasr_flash",
            "model_name": _BIGASR_RESOURCE_ID,
            "text": str(result.get("text", "")).strip(),
            "utterances": _normalize_utterances(result.get("utterances")),
            "audio_duration_ms": _safe_int(body.get("audio_info", {}).get("duration")),
            "request_id": current_request_id,
            "log_id": str(response.headers.get("X-Tt-Logid", "")).strip(),
        }

    def generate_subtitles(
        self,
        *,
        media_bytes: bytes,
        mime_type: str,
        language: str = "",
        caption_type: str = "auto",
        words_per_line: int | None = None,
        max_lines: int | None = None,
        use_itn: bool = True,
        use_punc: bool = True,
        use_ddc: bool = False,
        with_speaker_info: bool = False,
        use_capitalize: bool = True,
    ) -> dict[str, Any]:
        """Call Volcengine subtitle generation and wait for the final timed result."""
        submit_params = self._build_subtitle_params(
            language=language,
            caption_type=caption_type,
            words_per_line=words_per_line,
            max_lines=max_lines,
            use_itn=use_itn,
            use_punc=use_punc,
            use_ddc=use_ddc,
            with_speaker_info=with_speaker_info,
            use_capitalize=use_capitalize,
        )
        submit_response = self.session.post(
            _SUBTITLE_SUBMIT_URL,
            params=submit_params,
            data=media_bytes,
            headers={
                "Authorization": f"Bearer; {self.credentials.access_token}",
                "Content-Type": mime_type,
            },
            timeout=(30, 600),
        )
        submit_body = _decode_json_response(submit_response)
        job_id = _extract_job_id(
            submit_body,
            "Volcengine subtitle generation submit",
            expected_resource_id=_SUBTITLE_GENERATION_RESOURCE_ID,
            capability_name="SpeechRecognitionExpert subtitle generation (`task=subtitle`)",
        )
        query_body = self._query_subtitle_job(
            _SUBTITLE_QUERY_URL,
            job_id,
            expected_resource_id=_SUBTITLE_GENERATION_RESOURCE_ID,
            capability_name="SpeechRecognitionExpert subtitle generation (`task=subtitle`)",
        )
        return {
            "provider": "volcengine_subtitle_generation",
            "model_name": "volcengine_vc",
            "job_id": job_id,
            "text": _utterances_to_text(query_body.get("utterances")),
            "utterances": _normalize_utterances(query_body.get("utterances")),
            "audio_duration_ms": _seconds_to_milliseconds(query_body.get("duration")),
        }

    def align_subtitles(
        self,
        *,
        media_bytes: bytes,
        mime_type: str,
        subtitle_text: str,
        caption_type: str = "speech",
        sta_punc_mode: str = "",
    ) -> dict[str, Any]:
        """Call Volcengine automatic subtitle timing for one existing subtitle text."""
        files = {
            "data": ("media.wav", media_bytes, mime_type),
        }
        form_data = {
            "audio-text": subtitle_text,
        }
        submit_params: dict[str, Any] = {
            "appid": self.credentials.app_id,
            "caption_type": caption_type,
        }
        if sta_punc_mode:
            submit_params["sta_punc_mode"] = sta_punc_mode
        submit_response = self.session.post(
            _SUBTITLE_ALIGN_SUBMIT_URL,
            params=submit_params,
            files=files,
            data=form_data,
            headers={"Authorization": f"Bearer; {self.credentials.access_token}"},
            timeout=(30, 600),
        )
        submit_body = _decode_json_response(submit_response)
        job_id = _extract_job_id(
            submit_body,
            "Volcengine subtitle alignment submit",
            expected_resource_id=_SUBTITLE_ALIGNMENT_RESOURCE_ID,
            capability_name="SpeechRecognitionExpert subtitle timing (`subtitle_text` / `audio_text`)",
        )
        query_body = self._query_subtitle_job(
            _SUBTITLE_ALIGN_QUERY_URL,
            job_id,
            expected_resource_id=_SUBTITLE_ALIGNMENT_RESOURCE_ID,
            capability_name="SpeechRecognitionExpert subtitle timing (`subtitle_text` / `audio_text`)",
        )
        return {
            "provider": "volcengine_subtitle_alignment",
            "model_name": "volcengine_vc_ata",
            "job_id": job_id,
            "text": _utterances_to_text(query_body.get("utterances")),
            "utterances": _normalize_utterances(query_body.get("utterances")),
            "audio_duration_ms": _seconds_to_milliseconds(query_body.get("duration")),
        }

    def _build_subtitle_params(
        self,
        *,
        language: str,
        caption_type: str,
        words_per_line: int | None,
        max_lines: int | None,
        use_itn: bool,
        use_punc: bool,
        use_ddc: bool,
        with_speaker_info: bool,
        use_capitalize: bool,
    ) -> dict[str, Any]:
        """Build the query parameters for subtitle generation submit requests."""
        params: dict[str, Any] = {
            "appid": self.credentials.app_id,
            "caption_type": caption_type,
            "use_itn": str(bool(use_itn)),
            "use_punc": str(bool(use_punc)),
            "use_ddc": str(bool(use_ddc)),
            "with_speaker_info": str(bool(with_speaker_info)),
            "use_capitalize": str(bool(use_capitalize)),
        }
        if language:
            params["language"] = language
        if words_per_line is not None:
            params["words_per_line"] = int(words_per_line)
        if max_lines is not None:
            params["max_lines"] = int(max_lines)
        return params

    def _query_subtitle_job(
        self,
        query_url: str,
        job_id: str,
        *,
        expected_resource_id: str = "",
        capability_name: str = "",
    ) -> dict[str, Any]:
        """Query one submitted subtitle job and return the completed payload."""
        last_body: dict[str, Any] | None = None
        for _attempt in range(3):
            response = self.session.get(
                query_url,
                params={
                    "appid": self.credentials.app_id,
                    "id": job_id,
                    "blocking": 1,
                },
                headers={"Authorization": f"Bearer; {self.credentials.access_token}"},
                timeout=(30, 3600),
            )
            body = _decode_json_response(response)
            last_body = body if isinstance(body, dict) else {}
            code = _safe_int(last_body.get("code"))
            if response.status_code >= 400:
                raise RuntimeError(
                    _build_volcengine_error(
                        "Volcengine subtitle query",
                        body=last_body,
                        http_status=response.status_code,
                        code=code,
                        expected_resource_id=expected_resource_id,
                        capability_name=capability_name,
                    )
                )
            if code == 0:
                return last_body
            if code != 2000:
                raise RuntimeError(
                    _build_volcengine_error(
                        "Volcengine subtitle query",
                        body=last_body,
                        code=code,
                        expected_resource_id=expected_resource_id,
                        capability_name=capability_name,
                    )
                )
        raise RuntimeError(
            _build_volcengine_error(
                "Volcengine subtitle query did not finish in time",
                body=last_body or {},
                expected_resource_id=expected_resource_id,
                capability_name=capability_name,
            )
        )


def _decode_json_response(response: requests.Response) -> dict[str, Any]:
    """Decode one JSON HTTP response into a dictionary."""
    try:
        payload = response.json()
    except ValueError as exc:
        text = response.text[:500]
        raise RuntimeError(f"Volcengine returned invalid JSON: {text}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Volcengine returned unexpected payload: {payload!r}")
    return payload


def _extract_job_id(
    body: dict[str, Any],
    context: str,
    *,
    expected_resource_id: str = "",
    capability_name: str = "",
) -> str:
    """Validate one submit response and extract the returned job ID."""
    code = _safe_int(body.get("code"))
    if code != 0:
        raise RuntimeError(
            _build_volcengine_error(
                context,
                body=body,
                code=code,
                expected_resource_id=expected_resource_id,
                capability_name=capability_name,
            )
        )
    job_id = str(body.get("id", "")).strip()
    if not job_id:
        raise RuntimeError(
            _build_volcengine_error(
                f"{context} returned no job id",
                body=body,
                expected_resource_id=expected_resource_id,
                capability_name=capability_name,
            )
        )
    return job_id


def _build_volcengine_error(
    context: str,
    *,
    body: dict[str, Any],
    http_status: int | None = None,
    api_status: str = "",
    code: int | None = None,
    expected_resource_id: str = "",
    capability_name: str = "",
) -> str:
    """Build one consistent runtime error with an optional permission hint."""
    parts = [f"{context} failed:"]
    if http_status is not None:
        parts.append(f"http_status={http_status}")
    if api_status:
        parts.append(f"api_status={api_status}")
    if code is not None:
        parts.append(f"code={code}")
    parts.append(f"body={body}")
    detail = ", ".join(parts)
    permission_hint = _build_resource_permission_hint(
        body,
        expected_resource_id=expected_resource_id,
        capability_name=capability_name,
    )
    return f"{detail}{permission_hint}"


def _build_resource_permission_hint(
    body: dict[str, Any],
    *,
    expected_resource_id: str,
    capability_name: str,
) -> str:
    """Return one actionable hint when the backend reports missing grants."""
    message_text = str(body)
    lowered = message_text.lower()
    if not any(marker in lowered for marker in _RESOURCE_PERMISSION_MARKERS):
        return ""

    matched_resource = _extract_resource_id(message_text) or expected_resource_id
    if matched_resource:
        resource_text = f"`{matched_resource}`"
    else:
        resource_text = "the required speech resource"
    capability_text = f" for {capability_name}" if capability_name else ""
    return (
        f" Hint: the current Volcengine APPID / Access Token pair does not have access to {resource_text}"
        f"{capability_text}. Enable that resource in the Volcengine console, then retry."
    )


def _extract_resource_id(message_text: str) -> str:
    """Extract one `resource_id` token from an API error message when present."""
    match = _RESOURCE_ID_RE.search(message_text)
    if not match:
        return ""
    return match.group(1).strip()


def _normalize_utterances(value: Any) -> list[dict[str, Any]]:
    """Normalize the returned utterance list into a JSON-safe structure."""
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "text": str(item.get("text", "")).strip(),
                "start_time": _safe_int(item.get("start_time")),
                "end_time": _safe_int(item.get("end_time")),
                "words": item.get("words", []) if isinstance(item.get("words"), list) else [],
                "attribute": item.get("attribute", {}) if isinstance(item.get("attribute"), dict) else {},
            }
        )
    return normalized


def _utterances_to_text(value: Any) -> str:
    """Join the top-level utterance texts into one transcript."""
    utterances = _normalize_utterances(value)
    return "\n".join(item["text"] for item in utterances if item["text"]).strip()


def _seconds_to_milliseconds(value: Any) -> int | None:
    """Convert one duration expressed in seconds into milliseconds."""
    if value in ("", None):
        return None
    try:
        return int(round(float(value) * 1000))
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    """Convert one loosely typed integer-like value when possible."""
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
