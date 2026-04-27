# ADK Eval Assets

This directory contains ADK eval assets for CreativeClaw agent behavior.

Run the short-video P1b eval manually with live model credentials:

```bash
.venv/bin/adk eval tests/eval/creative_claw_orchestrator \
  tests/eval/evalsets/short_video_p0_evalset.json \
  --config_file_path tests/eval/eval_config.json \
  --print_detailed_results
```

Run the Design P0 eval manually with live model credentials:

```bash
.venv/bin/adk eval tests/eval/creative_claw_orchestrator \
  tests/eval/evalsets/design_p0_evalset.json \
  --config_file_path tests/eval/eval_config.json \
  --print_detailed_results
```

The deterministic unit tests `unit_test/test_short_video_adk_eval_assets.py` and
`unit_test/test_design_adk_eval_assets.py` validate that these files match the
ADK eval schemas. They do not run live model inference.
