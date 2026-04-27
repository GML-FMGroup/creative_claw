# Design Production

This package implements CreativeClaw's durable HTML-centered Design production product.

Design production is intentionally separate from one-shot image generation, editable PPTX generation, and video production. Its core artifact is always HTML. Screenshots, reports, generated images, PDFs, ZIP files, and handoff specs are secondary artifacts derived from or supporting the HTML artifact.

## Current P0a Flow

1. `start` creates a production session.
2. Reference assets are copied into the production session.
3. Deterministic placeholder brief, design system, and layout plan are prepared.
4. `PlaceholderHtmlBuilder` creates a single-file HTML artifact.
5. `HtmlValidator` checks portability, local references, structure, and duplicate ids.
6. `HtmlPreviewRenderer` attempts browser screenshots and records warnings if browser automation is unavailable.
7. Deterministic QC summarizes validator and preview results.
8. Final HTML, screenshots when available, and QC report are projected to ADK state.

## Non-placeholder Skeleton

When `placeholder_design=false`, `start` currently prepares a `design_direction_review` breakpoint. Approving that breakpoint builds the placeholder HTML and returns `preview_review`. P0b should replace the deterministic planning and placeholder HTML with internal structured Design experts while preserving the same state machine and review contracts.

## Package Responsibilities

- `tool.py`: ADK tool boundary for `run_design_production`.
- `manager.py`: production state machine, review checkpoints, revision handling, views, projection files, and final artifact projection.
- `models.py`: typed design state, brief, design system, layout plan, HTML artifact, preview report, and QC report models.
- `placeholders.py`: deterministic P0a HTML builder.
- `quality.py`: deterministic P0 quality report generation.
- `impact.py`: read-only P0 revision impact analysis.
- `tools/asset_ingestor.py`: reference asset registration and copying.
- `tools/html_validator.py`: static HTML validation.
- `tools/preview_renderer.py`: optional Playwright browser preview integration.

## Boundaries

- Keep `DesignProductionState` as the fact source.
- Keep ADK session state as projection only.
- Keep playbooks as SOP, not state or tool execution.
- Do not add a top-level `DesignExpert` that bypasses the production manager.
- Do not treat screenshots, PDFs, or images as replacements for the core HTML artifact.
