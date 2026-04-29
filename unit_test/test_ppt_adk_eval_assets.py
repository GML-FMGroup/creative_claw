import json
import unittest
from pathlib import Path

from google.adk.evaluation.eval_config import EvalConfig, get_eval_metrics_from_config
from google.adk.evaluation.eval_set import EvalSet


class PPTAdkEvalAssetsTests(unittest.TestCase):
    def test_ppt_eval_assets_match_adk_schemas(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        config_path = project_root / "tests" / "eval" / "eval_config.json"
        evalset_path = project_root / "tests" / "eval" / "evalsets" / "ppt_p0_evalset.json"

        eval_config = EvalConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        eval_set = EvalSet.model_validate_json(evalset_path.read_text(encoding="utf-8"))
        metrics = get_eval_metrics_from_config(eval_config)

        self.assertEqual(eval_set.eval_set_id, "ppt_p0")
        self.assertGreaterEqual(len(eval_set.eval_cases), 6)
        eval_ids = {item.eval_id for item in eval_set.eval_cases}
        self.assertIn("start_chinese_executive_report_requires_outline_review", eval_ids)
        self.assertIn("start_source_doc_ppt_uses_uploaded_context", eval_ids)
        self.assertIn("view_ppt_manifest_and_quality_state", eval_ids)
        self.assertIn("analyze_slide_revision_impact", eval_ids)
        self.assertIn("apply_confirmed_slide_revision", eval_ids)
        self.assertEqual(metrics[0].metric_name, "rubric_based_tool_use_quality_v1")
        self.assertEqual(metrics[0].threshold, 0.8)

        config_payload = json.loads(config_path.read_text(encoding="utf-8"))
        rubrics = config_payload["criteria"]["rubric_based_tool_use_quality_v1"]["rubrics"]
        rubric_ids = {item["rubric_id"] for item in rubrics}
        self.assertIn("ppt_uses_production_tool", rubric_ids)
        self.assertIn("ppt_review_gates_before_completion", rubric_ids)
        self.assertIn("ppt_source_documents_are_passed_as_inputs", rubric_ids)
        self.assertIn("ppt_view_for_state_manifest_or_quality", rubric_ids)
        self.assertIn("ppt_impact_before_targeted_revision", rubric_ids)
        self.assertIn("ppt_apply_revision_after_confirmation", rubric_ids)

        eval_payload = json.loads(evalset_path.read_text(encoding="utf-8"))
        source_doc_case = next(
            item
            for item in eval_payload["eval_cases"]
            if item["eval_id"] == "start_source_doc_ppt_uses_uploaded_context"
        )
        uploaded = source_doc_case["session_input"]["state"]["uploaded"]
        self.assertEqual(uploaded[0]["path"], "input/eval_q1_business_notes.md")
        self.assertIn("source", uploaded[0])

        for eval_case in eval_set.eval_cases:
            state = eval_case.session_input.state
            self.assertEqual(state["channel"], "eval")
            self.assertEqual(state["chat_id"], "ppt_p0")


if __name__ == "__main__":
    unittest.main()
