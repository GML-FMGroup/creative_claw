# ADK Eval Assets

This directory contains ADK eval assets for CreativeClaw agent behavior.

Run the short-video P1a eval manually with live model credentials:

```bash
.venv/bin/adk eval tests/eval/creative_claw_orchestrator \
  tests/eval/evalsets/short_video_p0_evalset.json \
  --config_file_path tests/eval/eval_config.json \
  --print_detailed_results
```

The deterministic unit test `unit_test/test_short_video_adk_eval_assets.py` validates
that these files match the ADK eval schemas. It does not run live model inference.
