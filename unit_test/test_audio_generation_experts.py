import base64
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.agents.experts.music_generation import tool as music_tool
from src.agents.experts.music_generation.music_generation_expert import MusicGenerationExpert
from src.agents.experts.speech_synthesis import tool as speech_tool
from src.agents.experts.speech_synthesis.speech_synthesis_expert import SpeechSynthesisExpert


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class SpeechSynthesisToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_bytedance_tts_tool_assembles_streamed_audio(self) -> None:
        encoded_audio = base64.b64encode(b"audio-bytes").decode("utf-8")
        captured_call: dict[str, object] = {}

        class _FakeResponse:
            status_code = 200
            headers = {"X-Tt-Logid": "log-123"}
            text = ""

            def iter_lines(self, decode_unicode=True):
                yield json.dumps({"code": 0, "data": encoded_audio})
                yield json.dumps({"code": 0, "sentence": {"text": "hello world"}})
                yield json.dumps({"code": 20000000, "usage": {"characters": 11}})

            def close(self) -> None:
                return None

        class _FakeSession:
            def post(self, url, headers=None, json=None, stream=None, timeout=None):
                captured_call["url"] = url
                captured_call["headers"] = headers
                captured_call["json"] = json
                captured_call["stream"] = stream
                captured_call["timeout"] = timeout
                return _FakeResponse()

            def close(self) -> None:
                return None

        with (
            patch.dict(
                os.environ,
                {
                    "VOLCENGINE_APPID": "app-id",
                    "VOLCENGINE_ACCESS_TOKEN": "access-token",
                },
                clear=False,
            ),
            patch("src.agents.experts.speech_synthesis.tool.requests.Session", return_value=_FakeSession()),
        ):
            result = await speech_tool.speech_synthesis_tool(
                user_id="user-1",
                text="hello world",
                speaker="demo-speaker",
                audio_format="mp3",
                enable_timestamp=True,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], b"audio-bytes")
        self.assertEqual(result["speaker"], "demo-speaker")
        self.assertEqual(result["usage"], {"characters": 11})
        self.assertEqual(result["log_id"], "log-123")
        self.assertEqual(captured_call["url"], "https://openspeech.bytedance.com/api/v3/tts/unidirectional")
        self.assertEqual(captured_call["headers"]["X-Api-App-Id"], "app-id")
        self.assertEqual(captured_call["json"]["req_params"]["text"], "hello world")
        self.assertTrue(captured_call["json"]["req_params"]["audio_params"]["enable_timestamp"])


class SpeechSynthesisExpertTests(unittest.IsolatedAsyncioTestCase):
    async def test_speech_synthesis_requires_text_or_ssml(self) -> None:
        agent = SpeechSynthesisExpert(name="SpeechSynthesisExpert")
        ctx = _build_ctx({"current_parameters": {}})

        events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: text or ssml", current_output["message"])


class MusicGenerationToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_music_generation_decodes_hex_audio_and_builds_instrumental_lyrics(self) -> None:
        captured_call: dict[str, object] = {}

        class _FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "base_resp": {"status_code": 0},
                    "data": {"audio": b"music-bytes".hex()},
                }

        def _fake_post(url, headers=None, json=None, timeout=None):
            captured_call["url"] = url
            captured_call["headers"] = headers
            captured_call["json"] = json
            captured_call["timeout"] = timeout
            return _FakeResponse()

        with (
            patch.dict(os.environ, {"MINIMAX_API_KEY": "minimax-key"}, clear=False),
            patch("src.agents.experts.music_generation.tool.requests.post", side_effect=_fake_post),
        ):
            result = await music_tool.music_generation_tool(
                prompt="cinematic orchestral background music",
                instrumental=True,
                audio_format="mp3",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], b"music-bytes")
        self.assertTrue(result["instrumental"])
        self.assertIn("[Inst]", result["lyrics_used"])
        self.assertEqual(captured_call["url"], "https://api.minimax.io/v1/music_generation")
        self.assertEqual(captured_call["headers"]["Authorization"], "Bearer minimax-key")
        self.assertEqual(captured_call["json"]["model"], "music-2.5")
        self.assertEqual(captured_call["json"]["prompt"], "cinematic orchestral background music")


class MusicGenerationExpertTests(unittest.IsolatedAsyncioTestCase):
    async def test_music_generation_requires_prompt(self) -> None:
        agent = MusicGenerationExpert(name="MusicGenerationExpert")
        ctx = _build_ctx({"current_parameters": {"instrumental": True}})

        events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: prompt", current_output["message"])


if __name__ == "__main__":
    unittest.main()
