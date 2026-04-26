from pathlib import Path
import unittest

from conf.agent import load_expert_configs
from conf.path import CONF_ROOT
from src.runtime.expert_cards import discover_expert_cards, parse_expert_card


class ExpertCardTests(unittest.TestCase):
    def _video_card_path(self) -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "experts"
            / "video_generation"
            / "EXPERT.md"
        )

    def _expert_card_path(self, *parts: str) -> Path:
        return Path(__file__).resolve().parents[1] / "src" / "agents" / "experts" / Path(*parts)

    def test_video_generation_expert_card_documents_audio_and_subtitle_boundaries(self) -> None:
        card_path = self._video_card_path()

        content = card_path.read_text(encoding="utf-8")

        self.assertIn("VideoGenerationAgent", content)
        self.assertIn("native audio", content)
        self.assertIn("SRT/VTT", content)
        self.assertIn("SpeechRecognitionExpert", content)
        self.assertIn("veo-3.1-generate-preview", content)
        self.assertIn("doubao-seedance-2-0-260128", content)
        self.assertIn("doubao-seedance-2-0-fast-260128", content)
        self.assertIn("kling-v1-6", content)
        self.assertNotIn("/Users/", content)

    def test_video_generation_expert_card_parses_to_prompt_description(self) -> None:
        card = parse_expert_card(self._video_card_path())

        description = card.build_description()
        parameters = card.build_parameters()

        self.assertEqual(card.name, "VideoGenerationAgent")
        self.assertIn("Use this expert for text-to-video", description)
        self.assertIn("Prefer `seedance`", description)
        self.assertIn("does not return structured subtitle files", description)
        self.assertIn("'provider': 'seedance|veo|kling'", parameters)
        self.assertIn("'mode': 'video_extension'", parameters)
        self.assertNotIn("##", description)

    def test_discover_expert_cards_finds_video_generation_card(self) -> None:
        cards = discover_expert_cards()

        self.assertIn("VideoGenerationAgent", cards)
        self.assertEqual(cards["VideoGenerationAgent"].metadata["default_provider"], "seedance")
        self.assertIn("ImageGenerationAgent", cards)
        self.assertIn("ImageEditingAgent", cards)
        self.assertIn("ImageUnderstandingAgent", cards)
        self.assertIn("SpeechRecognitionExpert", cards)

    def test_all_enabled_experts_have_expert_cards(self) -> None:
        expert_agents = load_expert_configs(Path(CONF_ROOT) / "jsons" / "agent.json")
        cards = discover_expert_cards()

        missing_cards = sorted(
            agent.name for agent in expert_agents if agent.enable and agent.name not in cards
        )

        self.assertEqual([], missing_cards)

    def test_expert_cards_do_not_embed_local_absolute_paths(self) -> None:
        cards = discover_expert_cards()

        for card in cards.values():
            self.assertNotIn("/Users/", card.path.read_text(encoding="utf-8"), card.name)

    def test_core_image_expert_cards_parse_routing_boundaries(self) -> None:
        generation_card = parse_expert_card(
            self._expert_card_path("image_generation", "EXPERT.md")
        )
        editing_card = parse_expert_card(
            self._expert_card_path("image_editing", "EXPERT.md")
        )
        understanding_card = parse_expert_card(
            self._expert_card_path("image_understanding", "EXPERT.md")
        )

        generation_description = generation_card.build_description()
        editing_description = editing_card.build_description()
        understanding_description = understanding_card.build_description()

        self.assertIn("text prompts only", generation_description)
        self.assertIn("use `ImageEditingAgent` instead", generation_description)
        self.assertIn("Always pass `input_path` or `input_paths`", editing_description)
        self.assertIn("call `ImageSegmentationAgent` first", editing_description)
        self.assertIn("Use mode `prompt`", understanding_description)
        self.assertIn("does not create image files", understanding_description)

    def test_speech_expert_cards_parse_subtitle_routing_boundaries(self) -> None:
        recognition_card = parse_expert_card(
            self._expert_card_path("speech_recognition", "EXPERT.md")
        )

        recognition_description = recognition_card.build_description()

        self.assertIn("Use `task=subtitle`", recognition_description)
        self.assertIn("SRT/VTT", recognition_description)
        self.assertIn("subtitle_path", recognition_description)

    def test_remaining_expert_cards_parse_core_boundaries(self) -> None:
        expected_phrases = {
            ("image_grounding", "EXPERT.md"): [
                "return bounding boxes",
                "DINO-XSeek-1.0",
                "does not save an output image or mask file",
            ],
            ("image_segmentation", "EXPERT.md"): [
                "save a binary mask image file",
                "DINO-X-1.0",
                "Reuse the returned `mask_path`",
            ],
            ("knowledge", "EXPERT.md"): [
                "professional visual design scheme",
                "returns text only",
                "does not create images",
            ],
            ("image_basic_operations", "EXPERT.md"): [
                "deterministic local image operations",
                "`crop`, `rotate`, `flip`, `info`, `resize`, and `convert`",
                "does not understand image content semantically",
            ],
            ("text_transform", "EXPERT.md"): [
                "exactly one atomic text transformation",
                "validates `mode` strictly",
                "does not read files",
            ],
            ("search", "EXPERT.md"): [
                "Serper image search",
                "DuckDuckGo",
                "does not judge truthfulness",
            ],
            ("video_understanding", "EXPERT.md"): [
                "analyze one or more workspace videos",
                "Use `prompt`",
                "does not generate, trim, transcode, or subtitle video files",
            ],
            ("video_basic_operations", "EXPERT.md"): [
                "deterministic local video operations",
                "`info`, `extract_frame`, `trim`, `concat`, and `convert`",
                "does not generate new footage",
            ],
            ("audio_basic_operations", "EXPERT.md"): [
                "deterministic local audio operations",
                "`info`, `trim`, `concat`, and `convert`",
                "does not transcribe speech",
            ],
            ("speech_synthesis", "EXPERT.md"): [
                "plain text or SSML",
                "default resource id is `seed-tts-1.0`",
                "does not transcribe audio",
            ],
            ("music_generation", "EXPERT.md"): [
                "MiniMax music generation",
                "code default model is `music-2.5`",
                "Keep voiceover and spoken narration requests routed to `SpeechSynthesisExpert`",
            ],
            ("three_d_generation", "EXPERT.md"): [
                "Provider `hy3d` remains the default",
                "Tencent Cloud Hunyuan 3D Pro",
                "doubao-seed3d-2-0-260328",
                "hyper3d-gen2-260112",
                "hitem3d-2-0-251223",
            ],
        }

        for path_parts, phrases in expected_phrases.items():
            card = parse_expert_card(self._expert_card_path(*path_parts))
            description = card.build_description()
            for phrase in phrases:
                self.assertIn(phrase, description, card.name)

    def test_agent_config_uses_expert_card_description_and_parameters(self) -> None:
        expert_agents = load_expert_configs(Path(CONF_ROOT) / "jsons" / "agent.json")
        generation_agent = next(agent for agent in expert_agents if agent.name == "ImageGenerationAgent")
        editing_agent = next(agent for agent in expert_agents if agent.name == "ImageEditingAgent")
        understanding_agent = next(agent for agent in expert_agents if agent.name == "ImageUnderstandingAgent")
        video_agent = next(agent for agent in expert_agents if agent.name == "VideoGenerationAgent")
        recognition_agent = next(agent for agent in expert_agents if agent.name == "SpeechRecognitionExpert")
        segmentation_agent = next(agent for agent in expert_agents if agent.name == "ImageSegmentationAgent")
        synthesis_agent = next(agent for agent in expert_agents if agent.name == "SpeechSynthesisExpert")
        music_agent = next(agent for agent in expert_agents if agent.name == "MusicGenerationExpert")
        three_d_agent = next(agent for agent in expert_agents if agent.name == "3DGeneration")

        self.assertIn("text prompts only", generation_agent.description)
        self.assertIn("'provider': 'nano_banana|seedream|gpt_image'", generation_agent.parameters)
        self.assertIn("modify one or more existing workspace images", editing_agent.description)
        self.assertIn("'provider': 'nano_banana|seedream'", editing_agent.parameters)
        self.assertIn("Use mode `prompt`", understanding_agent.description)
        self.assertIn("description|style|ocr|all|prompt", understanding_agent.parameters)
        self.assertIn("Use this expert for text-to-video", video_agent.description)
        self.assertIn("does not return structured subtitle files", video_agent.description)
        self.assertIn("'provider': 'seedance|veo|kling'", video_agent.parameters)
        self.assertIn("Use `task=subtitle`", recognition_agent.description)
        self.assertIn("subtitle_format", recognition_agent.parameters)
        self.assertIn("save a binary mask image file", segmentation_agent.description)
        self.assertIn("'model': 'DINO-X-1.0'", segmentation_agent.parameters)
        self.assertIn("default resource id is `seed-tts-1.0`", synthesis_agent.description)
        self.assertIn("audio_format", synthesis_agent.parameters)
        self.assertIn("code default model is `music-2.5`", music_agent.description)
        self.assertIn("instrumental", music_agent.parameters)
        self.assertIn("Provider `hy3d` remains the default", three_d_agent.description)
        self.assertIn("provider': 'seed3d'", three_d_agent.parameters)
        self.assertIn("provider': 'hyper3d'", three_d_agent.parameters)
        self.assertIn("provider': 'hitem3d'", three_d_agent.parameters)
        self.assertIn("result_format", three_d_agent.parameters)

    def test_minimal_agent_roster_is_enriched_by_expert_cards(self) -> None:
        expert_agents = load_expert_configs(Path(CONF_ROOT) / "jsons" / "agent.json")

        for agent in expert_agents:
            self.assertTrue(agent.description, agent.name)
            self.assertTrue(agent.parameters, agent.name)
