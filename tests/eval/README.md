# ADK Eval Assets

This directory contains ADK eval assets for CreativeClaw agent behavior.

Install the ADK eval extra before running live evals:

```bash
.venv/bin/pip install "google-adk[eval]>=1.29.0,<2.0.0"
```

Live evals call the configured agent model and judge model, so they require
working model credentials and network access. Run one or two eval cases first
while tuning routing behavior:

```bash
.venv/bin/adk eval tests/eval/creative_claw_orchestrator \
  tests/eval/evalsets/design_p0_evalset.json:start_saas_landing_requires_design_review \
  --config_file_path tests/eval/eval_config.json \
  --print_detailed_results
```

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
