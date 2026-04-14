import base64
import unittest

from src.agents.experts.speech_recognition.volcengine_client import (
    VolcengineSpeechClient,
    VolcengineSpeechCredentials,
)


class _FakeResponse:
    def __init__(self, payload, *, status_code=200, headers=None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, *, post_responses=None, get_responses=None) -> None:
        self.post_responses = list(post_responses or [])
        self.get_responses = list(get_responses or [])
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_responses.pop(0)

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    def close(self) -> None:
        return None


class VolcengineSpeechClientTests(unittest.TestCase):
    def test_recognize_flash_builds_expected_headers_and_payload(self) -> None:
        session = _FakeSession(
            post_responses=[
                _FakeResponse(
                    {
                        "result": {
                            "text": "hello world",
                            "utterances": [{"text": "hello world", "start_time": 0, "end_time": 1000}],
                        },
                        "audio_info": {"duration": 2400},
                    },
                    headers={"X-Api-Status-Code": "20000000", "X-Tt-Logid": "log-1"},
                )
            ]
        )
        client = VolcengineSpeechClient(
            VolcengineSpeechCredentials(app_id="app-1", access_token="token-1"),
            session=session,
        )

        result = client.recognize_flash(
            user_id="user-1",
            media_bytes=b"hello",
            language="en-US",
            enable_ddc=True,
        )

        self.assertEqual(result["provider"], "volcengine_bigasr_flash")
        self.assertEqual(result["text"], "hello world")
        self.assertEqual(result["audio_duration_ms"], 2400)
        url, kwargs = session.post_calls[0]
        self.assertIn("recognize/flash", url)
        self.assertEqual(kwargs["headers"]["X-Api-App-Key"], "app-1")
        self.assertEqual(kwargs["headers"]["X-Api-Access-Key"], "token-1")
        self.assertEqual(kwargs["json"]["audio"]["data"], base64.b64encode(b"hello").decode("utf-8"))
        self.assertEqual(kwargs["json"]["audio"]["language"], "en-US")
        self.assertTrue(kwargs["json"]["request"]["enable_ddc"])

    def test_generate_subtitles_submits_and_queries_job(self) -> None:
        session = _FakeSession(
            post_responses=[_FakeResponse({"code": 0, "id": "job-1"})],
            get_responses=[
                _FakeResponse(
                    {
                        "code": 0,
                        "duration": 1.4,
                        "utterances": [{"text": "hello world", "start_time": 0, "end_time": 1400}],
                    }
                )
            ],
        )
        client = VolcengineSpeechClient(
            VolcengineSpeechCredentials(app_id="app-1", access_token="token-1"),
            session=session,
        )

        result = client.generate_subtitles(
            media_bytes=b"wav-bytes",
            mime_type="audio/wav",
            language="zh-CN",
            caption_type="speech",
            words_per_line=15,
        )

        self.assertEqual(result["provider"], "volcengine_subtitle_generation")
        self.assertEqual(result["job_id"], "job-1")
        post_url, post_kwargs = session.post_calls[0]
        self.assertIn("/api/v1/vc/submit", post_url)
        self.assertEqual(post_kwargs["params"]["appid"], "app-1")
        self.assertEqual(post_kwargs["params"]["caption_type"], "speech")
        self.assertEqual(post_kwargs["headers"]["Authorization"], "Bearer; token-1")
        get_url, get_kwargs = session.get_calls[0]
        self.assertIn("/api/v1/vc/query", get_url)
        self.assertEqual(get_kwargs["params"]["id"], "job-1")

    def test_align_subtitles_uses_multipart_submit(self) -> None:
        session = _FakeSession(
            post_responses=[_FakeResponse({"code": 0, "id": "job-ata-1"})],
            get_responses=[
                _FakeResponse(
                    {
                        "code": 0,
                        "duration": 1.4,
                        "utterances": [{"text": "hello world", "start_time": 0, "end_time": 1400}],
                    }
                )
            ],
        )
        client = VolcengineSpeechClient(
            VolcengineSpeechCredentials(app_id="app-1", access_token="token-1"),
            session=session,
        )

        result = client.align_subtitles(
            media_bytes=b"wav-bytes",
            mime_type="audio/wav",
            subtitle_text="hello world",
            caption_type="speech",
            sta_punc_mode="2",
        )

        self.assertEqual(result["provider"], "volcengine_subtitle_alignment")
        post_url, post_kwargs = session.post_calls[0]
        self.assertIn("/api/v1/vc/ata/submit", post_url)
        self.assertEqual(post_kwargs["params"]["sta_punc_mode"], "2")
        self.assertEqual(post_kwargs["data"]["audio-text"], "hello world")
        self.assertEqual(post_kwargs["files"]["data"][2], "audio/wav")

    def test_recognize_flash_permission_error_has_actionable_hint(self) -> None:
        session = _FakeSession(
            post_responses=[
                _FakeResponse(
                    {
                        "header": {
                            "code": 45000030,
                            "message": "[resource_id=volc.bigasr.auc_turbo] requested resource not granted",
                        }
                    },
                    status_code=403,
                    headers={"X-Api-Status-Code": "45000030"},
                )
            ]
        )
        client = VolcengineSpeechClient(
            VolcengineSpeechCredentials(app_id="app-1", access_token="token-1"),
            session=session,
        )

        with self.assertRaisesRegex(RuntimeError, "volc.bigasr.auc_turbo"):
            client.recognize_flash(user_id="user-1", media_bytes=b"hello")

    def test_generate_subtitles_permission_error_has_actionable_hint(self) -> None:
        session = _FakeSession(
            post_responses=[
                _FakeResponse(
                    {
                        "code": 1022,
                        "message": "[resource_id=vc.async.default] requested resource not granted",
                    }
                )
            ]
        )
        client = VolcengineSpeechClient(
            VolcengineSpeechCredentials(app_id="app-1", access_token="token-1"),
            session=session,
        )

        with self.assertRaisesRegex(RuntimeError, "vc.async.default"):
            client.generate_subtitles(media_bytes=b"wav-bytes", mime_type="audio/wav")

    def test_align_subtitles_permission_error_uses_expected_resource_hint(self) -> None:
        session = _FakeSession(
            post_responses=[
                _FakeResponse(
                    {
                        "code": 1022,
                        "message": "requested grant not found",
                    }
                )
            ]
        )
        client = VolcengineSpeechClient(
            VolcengineSpeechCredentials(app_id="app-1", access_token="token-1"),
            session=session,
        )

        with self.assertRaisesRegex(RuntimeError, "volc.ata.default"):
            client.align_subtitles(
                media_bytes=b"wav-bytes",
                mime_type="audio/wav",
                subtitle_text="hello world",
            )


if __name__ == "__main__":
    unittest.main()
