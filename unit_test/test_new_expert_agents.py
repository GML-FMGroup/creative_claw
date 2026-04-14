import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

from google.genai.types import Content, Part

from src.agents.experts.speech_recognition import tool as recognition_tool
from src.agents.experts.speech_recognition.speech_recognition_expert import SpeechRecognitionExpert
from src.agents.experts.speech_transcription.speech_transcription_expert import SpeechTranscriptionExpert
from src.agents.experts.text_transform.text_transform_expert import TextTransformExpert
from src.agents.experts.video_understanding import tool as video_tool
from src.agents.experts.video_understanding.video_understanding_expert import VideoUnderstandingExpert
from src.runtime.workspace import workspace_relative_path, workspace_root


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class TextTransformExpertTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_transform_requires_mode(self) -> None:
        agent = TextTransformExpert(name="TextTransformExpert")
        ctx = _build_ctx({"current_parameters": {"input_text": "hello"}})

        events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: input_text or text, mode", current_output["message"])

    async def test_text_transform_returns_transformed_text(self) -> None:
        agent = TextTransformExpert(name="TextTransformExpert")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_text": "hello world",
                    "mode": "compress",
                }
            }
        )

        with patch(
            "src.agents.experts.text_transform.text_transform_expert.transform_text_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "hello",
                    "provider": "google_adk",
                    "model_name": "openai/gpt-5.4",
                }
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["transformed_text"], "hello")
        self.assertEqual(events[0].actions.state_delta["text_transform_results"]["mode"], "compress")


class VideoUnderstandingExpertTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_understanding_supports_prompt_mode(self) -> None:
        agent = VideoUnderstandingExpert(name="VideoUnderstandingExpert")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/demo.mp4",
                    "mode": "prompt",
                }
            }
        )

        with patch(
            "src.agents.experts.video_understanding.video_understanding_expert.video_understanding_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "prompt-result",
                    "analysis_text": "prompt-result",
                    "basic_info": "video-info",
                    "input_path": "inbox/session/demo.mp4",
                    "mode": "prompt",
                    "provider": "google_adk",
                    "model_name": "openai/gpt-5.4",
                }
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["results"][0]["mode"], "prompt")

    async def test_video_understanding_tool_builds_prompt_request(self) -> None:
        captured_request: dict[str, object] = {}

        class _FakeEvent:
            def __init__(self, text: str) -> None:
                self.content = Content(role="model", parts=[Part(text=text)])

            def is_final_response(self) -> bool:
                return True

        class _FakeLlmAgent:
            def __init__(self, **kwargs) -> None:
                self.before_model_callback = kwargs["before_model_callback"]

            async def run_async(self, ctx) -> AsyncGenerator[_FakeEvent, None]:
                llm_request = SimpleNamespace(contents=[])
                self.before_model_callback(SimpleNamespace(state={}), llm_request)
                captured_request["contents"] = llm_request.contents
                yield _FakeEvent("video reverse prompt")

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"fake-video-data")
            relative_path = workspace_relative_path(video_path)

            with (
                patch("src.agents.experts.video_understanding.tool.LlmAgent", _FakeLlmAgent),
                patch(
                    "src.agents.experts.video_understanding.tool.BuiltinToolbox.video_info",
                    return_value=json.dumps(
                        {
                            "duration_seconds": 1.2,
                            "width": 1280,
                            "height": 720,
                            "fps": 24,
                            "video_codec": "h264",
                            "audio_codec": "aac",
                        }
                    ),
                ),
            ):
                result = await video_tool.video_understanding_tool(
                    _build_ctx({}),
                    relative_path,
                    mode="prompt",
                )

        self.assertEqual(result["status"], "success")
        self.assertIn("video reverse prompt", result["analysis_text"])
        self.assertIn("Basic video info: duration_seconds=1.2", result["message"])
        self.assertIn("Reverse engineer a reusable creative prompt", captured_request["contents"][0].parts[0].text)


class SpeechRecognitionExpertTests(unittest.IsolatedAsyncioTestCase):
    async def test_speech_recognition_requires_input_path(self) -> None:
        agent = SpeechRecognitionExpert(name="SpeechRecognitionExpert")
        ctx = _build_ctx({"current_parameters": {"timestamps": True}})

        events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: input_path or input_paths", current_output["message"])

    async def test_speech_recognition_tool_formats_volcengine_utterances(self) -> None:
        captured_call: dict[str, object] = {}

        class _FakeVolcengineSpeechClient:
            def recognize_flash(self, **kwargs):
                captured_call.update(kwargs)
                return {
                    "provider": "volcengine_bigasr_flash",
                    "model_name": "volc.bigasr.auc_turbo",
                    "text": "hello world",
                    "utterances": [
                        {
                            "text": "hello world",
                            "start_time": 0,
                            "end_time": 1400,
                            "words": [],
                            "attribute": {},
                        }
                    ],
                    "audio_duration_ms": 2400,
                    "request_id": "req-1",
                    "log_id": "log-1",
                }

            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            audio_path = Path(tmp_dir) / "demo.wav"
            audio_path.write_bytes(b"fake-audio-data")
            relative_path = workspace_relative_path(audio_path)

            with (
                patch(
                    "src.agents.experts.speech_recognition.tool._prepare_media_for_volcengine",
                    return_value=recognition_tool.PreparedMedia(
                        input_path=relative_path,
                        prepared_path=audio_path,
                        mime_type="audio/wav",
                        media_bytes=b"prepared-wav",
                    ),
                ),
                patch(
                    "src.agents.experts.speech_recognition.tool.describe_media_metadata",
                    return_value="Basic media info: duration_seconds=2.4, sample_rate=16000, channels=1, codec=pcm_s16le.",
                ),
                patch("src.agents.experts.speech_recognition.tool.VolcengineSpeechClient", _FakeVolcengineSpeechClient),
            ):
                result = await recognition_tool.speech_recognition_tool(
                    _build_ctx({}),
                    relative_path,
                    language="en",
                    timestamps=True,
                    task="asr",
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["transcription_text"], "[00:00.000] hello world")
        self.assertEqual(result["request_id"], "req-1")
        self.assertEqual(result["log_id"], "log-1")
        self.assertEqual(captured_call["language"], "en-US")
        self.assertEqual(captured_call["media_bytes"], b"prepared-wav")

    async def test_speech_recognition_subtitle_mode_writes_subtitle_file(self) -> None:
        agent = SpeechRecognitionExpert(name="SpeechRecognitionExpert")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/demo.wav",
                    "task": "subtitle",
                    "subtitle_format": "vtt",
                },
                "step": 0,
            }
        )

        with patch(
            "src.agents.experts.speech_recognition.speech_recognition_expert.speech_subtitle_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "Subtitle generation completed",
                    "transcription_text": "[00:00.000] hello world",
                    "basic_info": "Basic media info: duration_seconds=2.4.",
                    "input_path": "inbox/session/demo.wav",
                    "provider": "volcengine_subtitle_generation",
                    "model_name": "volcengine_vc",
                    "task": "subtitle",
                    "timestamps": True,
                    "subtitle_content": "WEBVTT\n\n00:00:00.000 --> 00:00:01.400\nhello world\n",
                    "subtitle_backend": "volcengine_subtitle_generation",
                    "caption_type": "auto",
                    "job_id": "job-1",
                }
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        result = current_output["results"][0]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(result["task"], "subtitle")
        self.assertTrue(result["subtitle_path"].endswith(".vtt"))

        subtitle_file = workspace_root() / result["subtitle_path"]
        self.assertTrue(subtitle_file.exists())
        self.assertTrue(subtitle_file.read_text(encoding="utf-8").startswith("WEBVTT"))
        subtitle_file.unlink()

    async def test_speech_recognition_auto_uses_subtitle_tool_when_subtitle_text_present(self) -> None:
        agent = SpeechRecognitionExpert(name="SpeechRecognitionExpert")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/demo.wav",
                    "subtitle_text": "hello world",
                }
            }
        )

        subtitle_mock = AsyncMock(
            return_value={
                "status": "success",
                "message": "Subtitle generation completed",
                "transcription_text": "[00:00.000] hello world",
                "basic_info": "Basic media info: duration_seconds=2.4.",
                "input_path": "inbox/session/demo.wav",
                "provider": "volcengine_subtitle_alignment",
                "model_name": "volcengine_vc_ata",
                "task": "subtitle",
                "timestamps": True,
                "subtitle_content": "1\n00:00:00,000 --> 00:00:01,400\nhello world\n",
                "subtitle_backend": "volcengine_subtitle_alignment",
                "caption_type": "speech",
                "job_id": "job-ata-1",
            }
        )
        asr_mock = AsyncMock()

        with (
            patch(
                "src.agents.experts.speech_recognition.speech_recognition_expert.speech_subtitle_tool",
                new=subtitle_mock,
            ),
            patch(
                "src.agents.experts.speech_recognition.speech_recognition_expert.speech_recognition_tool",
                new=asr_mock,
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertTrue(subtitle_mock.await_count == 1)
        self.assertEqual(asr_mock.await_count, 0)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["results"][0]["subtitle_backend"], "volcengine_subtitle_alignment")

    async def test_speech_transcription_alias_still_works(self) -> None:
        agent = SpeechTranscriptionExpert(name="SpeechTranscriptionExpert")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/demo.wav",
                }
            }
        )

        with patch(
            "src.agents.experts.speech_recognition.speech_recognition_expert.speech_recognition_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "plain transcript",
                    "transcription_text": "plain transcript",
                    "basic_info": "Basic media info: duration_seconds=2.4.",
                    "input_path": "inbox/session/demo.wav",
                    "provider": "google_adk",
                    "model_name": "volc.bigasr.auc_turbo",
                    "task": "asr",
                    "timestamps": False,
                }
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(events[0].actions.state_delta["speech_transcription_results"][0]["task"], "asr")


if __name__ == "__main__":
    unittest.main()
