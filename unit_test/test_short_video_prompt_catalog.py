import unittest

from src.production.short_video import manager
from src.production.short_video.prompt_catalog import (
    PromptCatalogError,
    available_prompt_templates,
    render_prompt_template,
)


class ShortVideoPromptCatalogTest(unittest.TestCase):
    def test_packaged_prompt_templates_are_discoverable(self):
        templates = set(available_prompt_templates())

        self.assertIn("product_ad_visual", templates)
        self.assertIn("cartoon_short_drama_visual", templates)
        self.assertIn("social_media_visual", templates)
        self.assertIn("native_audio_dialogue", templates)
        self.assertIn("shot_segment_visual", templates)

    def test_template_rendering_replaces_all_placeholders(self):
        prompt = render_prompt_template(
            "product_ad_visual",
            {
                "brief": "Instinct cat food ad",
                "reference_note": "Use uploaded product package as identity anchor.",
                "storyboard_instruction": "Shot 1: product reveal.",
                "native_audio_instruction": "Native audio instructions: soft Chinese voiceover.",
            },
        )

        self.assertIn("Instinct cat food ad", prompt)
        self.assertIn("Use uploaded product package", prompt)
        self.assertNotIn("{{", prompt)
        self.assertNotIn("}}", prompt)

    def test_missing_template_variable_fails_loudly(self):
        with self.assertRaises(PromptCatalogError):
            render_prompt_template(
                "product_ad_visual",
                {
                    "brief": "brief only",
                },
            )

    def test_template_name_cannot_escape_prompt_package(self):
        with self.assertRaises(PromptCatalogError):
            render_prompt_template("../manager", {})

    def test_dialogue_audio_prompt_preserves_character_dialogue_rule(self):
        prompt = manager._build_native_audio_instruction(
            "猫A: 你妈妈一个月赚多少钱？\n猫B: 两万五\n不用显示字幕。语音风格软萌。"
        )

        self.assertIn('猫A says "你妈妈一个月赚多少钱？"', prompt)
        self.assertIn('猫B says "两万五"', prompt)
        self.assertIn("with no narrator reading the task description", prompt)
        self.assertIn("Do not render subtitles", prompt)


if __name__ == "__main__":
    unittest.main()
