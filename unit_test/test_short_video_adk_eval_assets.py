import json
import unittest
from pathlib import Path

from google.adk.evaluation.eval_config import EvalConfig, get_eval_metrics_from_config
from google.adk.evaluation.eval_set import EvalSet


class ShortVideoAdkEvalAssetsTests(unittest.TestCase):
    def test_short_video_eval_assets_match_adk_schemas(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        config_path = project_root / "tests" / "eval" / "eval_config.json"
        evalset_path = project_root / "tests" / "eval" / "evalsets" / "short_video_p0_evalset.json"

        eval_config = EvalConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        eval_set = EvalSet.model_validate_json(evalset_path.read_text(encoding="utf-8"))
        metrics = get_eval_metrics_from_config(eval_config)

        self.assertEqual(eval_set.eval_set_id, "short_video_p0")
        self.assertGreaterEqual(len(eval_set.eval_cases), 8)
        eval_ids = {item.eval_id for item in eval_set.eval_cases}
        self.assertIn("start_seedance_fast_short_requires_review", eval_ids)
        self.assertIn("start_product_ad_with_reference_requires_review", eval_ids)
        self.assertEqual(metrics[0].metric_name, "rubric_based_tool_use_quality_v1")
        self.assertEqual(metrics[0].threshold, 0.8)

        config_payload = json.loads(config_path.read_text(encoding="utf-8"))
        rubrics = config_payload["criteria"]["rubric_based_tool_use_quality_v1"]["rubrics"]
        rubric_ids = {item["rubric_id"] for item in rubrics}
        self.assertIn("short_video_uses_production_tool", rubric_ids)
        self.assertIn("short_video_impact_before_targeted_revision", rubric_ids)
        self.assertIn("short_video_apply_revision_after_confirmation", rubric_ids)
