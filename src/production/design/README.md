# Design Production

This package implements CreativeClaw's durable HTML-centered Design production product.

Design production is intentionally separate from one-shot image generation, editable PPTX generation, and video production. Its core artifact is always HTML. Screenshots, reports, generated images, ZIP bundles, and handoff specs are secondary artifacts derived from or supporting the HTML artifact.

## Current P0a Flow

1. `start` creates a production session.
2. Reference assets are copied into the production session.
3. Deterministic placeholder brief, design system, and layout plan are prepared.
4. `PlaceholderHtmlBuilder` creates a single-file HTML artifact.
5. `HtmlValidator` checks portability, local references, structure, and duplicate ids.
6. `HtmlPreviewRenderer` attempts browser screenshots and records warnings if browser automation is unavailable.
7. Deterministic QC summarizes validator and preview results.
8. Final HTML, screenshots when available, and QC report are projected to ADK state.

## P0b-A Non-placeholder Flow

When `placeholder_design=false`, `start` now uses internal structured Design experts to prepare the brief, design system, and layout plan, then pauses at `design_direction_review`. Approving that breakpoint calls `HtmlBuilderExpert` to generate a baseline single-file HTML artifact, then runs static validation, optional browser preview, deterministic QC, and supplemental expert QC before pausing at `preview_review`.

The internal experts are encapsulated behind `DesignProductionManager`; they are not top-level orchestrator experts and do not own production state.

## P0b-B Revision Flow

At `preview_review`, a `decision=revise` response now runs revision impact analysis from `DesignProductionState`, marks previous HTML artifacts stale, and asks `HtmlBuilderExpert` for a full-page revision build. P0 keeps section-aware impact metadata, but still rebuilds the single-page HTML artifact instead of assembling section fragments. The rebuilt artifact uses `HtmlBuilderExpert.variant`, then runs validator, preview, deterministic QC, and supplemental expert QC before returning to `preview_review`.

## P0b-C Expert Quality Feedback

Expert and revision HTML builds now run supplemental `DesignQCExpert` assessment after validator and preview facts are available. Deterministic validator and preview checks remain the hard fact source; expert findings are merged as informational or warning-level guidance, and expert QC failure records a warning instead of failing production.

## P0b-E Lightweight Handoff

When a Design production run reaches final approval, the manager now writes deterministic handoff exports under `exports/`: `design_spec.md` for human review and `handoff_manifest.json` for machine-readable downstream use. These files are derived from `DesignProductionState`, recorded in `export_artifacts`, and projected with the final HTML, preview screenshots, and QC report. P0b-E does not generate PDF, ZIP, Figma, or production-code handoff outputs.

## P1a Handoff Bundle

Final approval now also writes `exports/design_handoff_bundle.zip`, a portable bundle for downstream handoff. The bundle includes available final deliverables such as the approved HTML artifact, preview screenshots, QC report, `design_spec.md`, and `handoff_manifest.json`. P1a introduced the bundle without Figma or production-code handoff outputs.

## P1b Source References

Design outputs now expose source reference details for user-provided reference assets. HTML artifacts, preview reports, read-only preview/artifacts views, `design_spec.md`, and `handoff_manifest.json` include source asset names, workspace-relative paths, kinds, and statuses where available. This keeps handoff output traceable without exposing local absolute paths.

## P1c Review Quality Metadata

The `preview_review` payload now uses shared `ReviewPayload.metadata` for compact delivery, preview, quality, and source-reference summaries. `view_type="overview"` exposes the same active review metadata, so runtime clients can show approval context without re-parsing full HTML artifact, preview report, or QC report payloads.

## P1d Optional PDF Export

Design production can now export the approved HTML artifact to `exports/design.pdf` when PDF is explicitly requested through `design_settings.exports` or the final preview approval response. PDF remains a derived handoff artifact: HTML is still the durable source of truth, and missing browser export dependencies create a non-blocking PDF export report instead of failing production.

## P1e Design Token Handoff

Final approval now exports `exports/design_tokens.json` and `exports/design_tokens.css` when a `DesignSystemSpec` exists. These files are deterministic handoff artifacts derived from the production design system: JSON preserves the structured token payload, while CSS exposes stable `--cc-*` custom properties for downstream implementation.

## P1f Design System Audit

Design production now runs a deterministic design-system audit whenever a Design system is prepared. The report checks token coverage, naming collisions, color values and contrast, typography roles, spacing/radius constraints, and component-token coverage. Audit output is non-blocking, persisted under `reports/design_system_audit.*`, exposed through Design system and quality views, and included in handoff exports.

## P1g Component Inventory

Design production now derives an implementation-facing component inventory from `LayoutPlan`, `DesignSystemSpec`, and the generated HTML artifact. The report lists layout sections, tokenized components, and detected HTML component hooks with selectors, source refs, token refs, and implementation notes. Inventory output is persisted under `reports/component_inventory.*`, exposed through `view_type="components"`, and included in handoff exports.

## P1h Browser Diagnostics

Design production now derives deterministic browser diagnostics from preview and PDF export facts. The report separates environment issues, missing preview artifacts, browser console/network failures, responsive overflow, and PDF export failures without making browser-dependent outputs blocking. Diagnostics are persisted under `reports/browser_diagnostics.*`, exposed through `view_type="diagnostics"`, included in preview review metadata, and packaged in final handoff exports.

## P1i Artifact Lineage

Design production now derives deterministic HTML artifact lineage from `DesignProductionState`. The report links each HTML artifact to version, status, builder mode, revision id, source refs, validation/component/preview/diagnostics/QC/PDF reports, preview screenshots, PDF exports, and stale/replaced relationships. Lineage is persisted under `reports/artifact_lineage.*`, exposed through `view_type="lineage"`, included in preview review metadata, and packaged in final handoff exports.

## Package Responsibilities

- `tool.py`: ADK tool boundary for `run_design_production`.
- `manager.py`: production state machine, review checkpoints, revision handling, views, projection files, and final artifact projection.
- `models.py`: typed design state, brief, design system, layout plan, HTML artifact, preview report, PDF export report, and QC report models.
- `artifact_lineage.py`: deterministic HTML artifact lineage reports linking revisions, artifacts, and derived report ids.
- `browser_diagnostics.py`: deterministic diagnostics derived from browser preview and PDF export reports.
- `design_system_audit.py`: deterministic Design system audit rules and report rendering.
- `component_inventory.py`: deterministic component inventory extraction from state and generated HTML.
- `placeholders.py`: deterministic P0a HTML builder.
- `expert_runtime.py`: internal ADK structured-output experts for non-placeholder Design direction, HTML generation, and supplemental quality feedback.
- `handoff.py`: deterministic Design spec, handoff manifest, and ZIP bundle exports for completed production runs.
- `source_refs.py`: source-reference enrichment helpers for views, reviews, and handoff files.
- `tokens.py`: deterministic JSON and CSS token handoff exports derived from `DesignSystemSpec`.
- `prompt_catalog.py` and `prompts/`: packaged prompt templates used by the internal Design experts.
- `quality.py`: deterministic P0 quality report generation and supplemental expert finding merge.
- `impact.py`: read-only P0 revision impact analysis.
- `tools/asset_ingestor.py`: reference asset registration and copying.
- `tools/html_validator.py`: static HTML validation.
- `tools/pdf_exporter.py`: optional browser-based HTML-to-PDF export.
- `tools/preview_renderer.py`: optional Playwright browser preview integration.

## Boundaries

- Keep `DesignProductionState` as the fact source.
- Keep ADK session state as projection only.
- Keep playbooks as SOP, not state or tool execution.
- Do not add a top-level `DesignExpert` that bypasses the production manager.
- Do not treat screenshots, PDFs, or images as replacements for the core HTML artifact.
