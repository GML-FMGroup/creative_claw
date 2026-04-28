import importlib.util
import json
import unittest
from pathlib import Path

from google.adk.evaluation.eval_config import EvalConfig, get_eval_metrics_from_config
from google.adk.evaluation.eval_set import EvalSet


class DesignAdkEvalAssetsTests(unittest.TestCase):
    def test_design_eval_assets_match_adk_schemas(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        config_path = project_root / "tests" / "eval" / "eval_config.json"
        evalset_path = project_root / "tests" / "eval" / "evalsets" / "design_p0_evalset.json"

        eval_config = EvalConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        eval_set = EvalSet.model_validate_json(evalset_path.read_text(encoding="utf-8"))
        metrics = get_eval_metrics_from_config(eval_config)

        self.assertEqual(eval_set.eval_set_id, "design_p0")
        self.assertGreaterEqual(len(eval_set.eval_cases), 9)
        eval_ids = {item.eval_id for item in eval_set.eval_cases}
        self.assertIn("start_saas_landing_requires_design_review", eval_ids)
        self.assertIn("start_dashboard_ui_requires_design_review", eval_ids)
        self.assertIn("view_preview_and_quality_after_generation", eval_ids)
        self.assertIn("start_multi_page_microsite_preserves_pages", eval_ids)
        self.assertIn("analyze_hero_revision_impact", eval_ids)
        self.assertIn("apply_confirmed_hero_revision", eval_ids)
        self.assertIn("poster_png_should_not_use_design_production", eval_ids)
        self.assertIn("editable_ppt_should_not_use_design_production", eval_ids)
        self.assertEqual(metrics[0].metric_name, "rubric_based_tool_use_quality_v1")
        self.assertEqual(metrics[0].threshold, 0.8)

        config_payload = json.loads(config_path.read_text(encoding="utf-8"))
        rubrics = config_payload["criteria"]["rubric_based_tool_use_quality_v1"]["rubrics"]
        rubric_ids = {item["rubric_id"] for item in rubrics}
        self.assertIn("design_uses_production_tool", rubric_ids)
        self.assertIn("design_review_before_html_generation", rubric_ids)
        self.assertIn("design_multi_page_preserves_page_intent", rubric_ids)
        self.assertIn("design_view_for_preview_or_quality", rubric_ids)
        self.assertIn("design_impact_before_targeted_revision", rubric_ids)
        self.assertIn("design_apply_revision_after_confirmation", rubric_ids)
        self.assertIn("design_boundary_for_non_html_outputs", rubric_ids)

        eval_payload = json.loads(evalset_path.read_text(encoding="utf-8"))
        multi_page_case = next(
            item
            for item in eval_payload["eval_cases"]
            if item["eval_id"] == "start_multi_page_microsite_preserves_pages"
        )
        user_text = multi_page_case["conversation"][0]["user_content"]["parts"][0]["text"]
        self.assertIn("多页面 HTML microsite", user_text)
        self.assertIn("index.html", user_text)
        self.assertIn("product.html", user_text)
        self.assertIn("pricing.html", user_text)

        for eval_case in eval_set.eval_cases:
            state = eval_case.session_input.state
            self.assertEqual(state["channel"], "eval")
            self.assertEqual(state["chat_id"], "design_p0")

    def test_design_eval_agent_has_image_boundary_stub(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        agent_path = project_root / "tests" / "eval" / "creative_claw_orchestrator" / "agent.py"
        spec = importlib.util.spec_from_file_location("creative_claw_design_eval_agent_test", agent_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        image_stub = module._orchestrator.expert_agents.get("ImageGenerationAgent")
        self.assertIsNotNone(image_stub)
        self.assertEqual(image_stub.name, "ImageGenerationAgent")


if __name__ == "__main__":
    unittest.main()
