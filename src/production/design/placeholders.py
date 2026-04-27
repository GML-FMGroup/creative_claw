"""Placeholder HTML generation for the Design P0a production pipeline."""

from __future__ import annotations

import html
from pathlib import Path

from src.production.design.models import DesignProductionState, HtmlArtifact
from src.runtime.workspace import workspace_relative_path


class PlaceholderHtmlBuilder:
    """Build a deterministic single-file HTML artifact for P0a validation."""

    def build(self, *, session_root: Path, state: DesignProductionState) -> HtmlArtifact:
        """Write one self-contained HTML file and return its artifact record."""
        if state.layout_plan is None or not state.layout_plan.pages:
            raise ValueError("layout_plan with at least one page is required")
        page = state.layout_plan.pages[0]
        output_dir = session_root / "artifacts"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / page.path
        output_path.write_text(_render_placeholder_html(state), encoding="utf-8")
        return HtmlArtifact(
            page_id=page.page_id,
            path=workspace_relative_path(output_path),
            builder="placeholder",
            section_fragments={section.section_id: section.title for section in page.sections},
            depends_on=[asset.asset_id for asset in state.reference_assets if asset.status == "valid"],
            status="draft",
            metadata={"page_title": page.title, "build_mode": state.build_mode},
        )


def _render_placeholder_html(state: DesignProductionState) -> str:
    brief = state.brief
    design_system = state.design_system
    page = state.layout_plan.pages[0] if state.layout_plan and state.layout_plan.pages else None
    title = html.escape(page.title if page else "Design Preview")
    goal = html.escape(brief.goal if brief else "Generated HTML design preview")
    audience = html.escape(brief.audience if brief else "Target audience")
    primary_action = html.escape(brief.primary_action if brief else "Get started")
    primary_color = "#165DFF"
    accent_color = "#00A878"
    ink_color = "#18202F"
    surface_color = "#F6F8FB"
    if design_system is not None and design_system.colors:
        primary_color = design_system.colors[0].value
        if len(design_system.colors) > 1:
            accent_color = design_system.colors[1].value
        if len(design_system.colors) > 2:
            ink_color = design_system.colors[2].value
        if len(design_system.colors) > 3:
            surface_color = design_system.colors[3].value

    sections = page.sections if page else []
    rendered_sections = "\n".join(_render_section(index, section) for index, section in enumerate(sections, start=1))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --primary: {primary_color};
      --accent: {accent_color};
      --ink: {ink_color};
      --surface: {surface_color};
      --line: #DCE3EC;
      --muted: #667085;
      --radius: 8px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--surface);
      line-height: 1.55;
      letter-spacing: 0;
    }}
    .shell {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
    }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }}
    .nav {{
      min-height: 68px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 760;
    }}
    .brand-mark {{
      width: 34px;
      height: 34px;
      border-radius: var(--radius);
      background: var(--primary);
    }}
    .nav-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 0 16px;
      border-radius: var(--radius);
      border: 1px solid var(--primary);
      background: var(--primary);
      color: #ffffff;
      font-weight: 700;
      text-decoration: none;
      white-space: nowrap;
    }}
    .button.secondary {{
      color: var(--primary);
      background: #ffffff;
    }}
    .hero {{
      padding: 72px 0 52px;
      background: #ffffff;
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
      gap: 42px;
      align-items: center;
    }}
    h1 {{
      margin: 0;
      max-width: 760px;
      font-size: 56px;
      line-height: 1.02;
      font-weight: 820;
      letter-spacing: 0;
    }}
    .lead {{
      margin: 20px 0 0;
      max-width: 680px;
      color: var(--muted);
      font-size: 19px;
    }}
    .hero-panel {{
      min-height: 360px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: linear-gradient(180deg, #F9FBFF, #EEF6F2);
      padding: 20px;
      display: grid;
      gap: 14px;
      align-content: center;
    }}
    .metric-row {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }}
    .metric, .panel-block, .section {{
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #ffffff;
    }}
    .metric {{
      padding: 14px;
    }}
    .metric strong {{
      display: block;
      font-size: 24px;
      color: var(--primary);
    }}
    .panel-block {{
      min-height: 120px;
      padding: 18px;
    }}
    main {{
      padding: 32px 0 72px;
    }}
    .section {{
      padding: 28px;
      margin-top: 18px;
    }}
    .section-label {{
      color: var(--accent);
      font-weight: 760;
      text-transform: uppercase;
      font-size: 12px;
    }}
    .section h2 {{
      margin: 8px 0 8px;
      font-size: 26px;
      line-height: 1.18;
    }}
    .section ul {{
      margin: 14px 0 0;
      padding-left: 20px;
      color: var(--muted);
    }}
    footer {{
      padding: 28px 0;
      color: var(--muted);
      border-top: 1px solid var(--line);
      background: #ffffff;
    }}
    @media (max-width: 820px) {{
      .hero {{
        padding: 44px 0 28px;
      }}
      .hero-grid {{
        grid-template-columns: 1fr;
      }}
      h1 {{
        font-size: 40px;
      }}
      .lead {{
        font-size: 17px;
      }}
      .metric-row {{
        grid-template-columns: 1fr;
      }}
      .nav {{
        align-items: flex-start;
        flex-direction: column;
        padding: 16px 0;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="shell nav">
      <div class="brand"><span class="brand-mark" aria-hidden="true"></span><span>{title}</span></div>
      <div class="nav-actions">
        <a class="button secondary" href="#overview">Overview</a>
        <a class="button" href="#contact">{primary_action}</a>
      </div>
    </div>
  </header>
  <section class="hero" id="page-hero">
    <div class="shell hero-grid">
      <div>
        <h1>{goal}</h1>
        <p class="lead">Designed for {audience}. This P0a artifact validates the production workflow, responsive HTML structure, stable section ids, and artifact projection.</p>
        <p><a class="button" href="#contact">{primary_action}</a></p>
      </div>
      <div class="hero-panel" aria-label="Design preview composition">
        <div class="metric-row">
          <div class="metric"><strong>HTML</strong><span>core artifact</span></div>
          <div class="metric"><strong>2</strong><span>viewports</span></div>
          <div class="metric"><strong>P0a</strong><span>pipeline</span></div>
        </div>
        <div class="panel-block">Brand, content hierarchy, and section structure are preserved in typed production state.</div>
      </div>
    </div>
  </section>
  <main class="shell" id="overview">
    {rendered_sections}
  </main>
  <footer id="contact">
    <div class="shell">Generated by CreativeClaw Design production. HTML is the durable source artifact.</div>
  </footer>
</body>
</html>
"""


def _render_section(index: int, section) -> str:
    content_items = "".join(f"<li>{html.escape(item)}</li>" for item in section.content)
    return f"""<section class="section" id="{html.escape(section.section_id)}">
  <div class="section-label">Section {index}</div>
  <h2>{html.escape(section.title)}</h2>
  <p>{html.escape(section.purpose)}</p>
  <ul>{content_items}</ul>
</section>"""
