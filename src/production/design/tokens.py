"""Design token handoff exports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.production.design.models import DesignProductionState, DesignSystemSpec
from src.production.models import WorkspaceFileRef, utc_now_iso
from src.runtime.workspace import workspace_relative_path


TOKEN_JSON_NAME = "design_tokens.json"
TOKEN_CSS_NAME = "design_tokens.css"


def write_design_token_exports(
    *,
    state: DesignProductionState,
    export_dir: Path,
) -> list[WorkspaceFileRef]:
    """Write handoff-friendly token JSON and CSS files for the current design system."""
    if state.design_system is None:
        return []

    export_dir.mkdir(parents=True, exist_ok=True)
    css_variables = design_system_css_variables(state.design_system)
    json_path = export_dir / TOKEN_JSON_NAME
    css_path = export_dir / TOKEN_CSS_NAME

    json_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "generated_at": utc_now_iso(),
                "production_session_id": state.production_session.production_session_id,
                "design_system_id": state.design_system.design_system_id,
                "design_system_version": state.design_system.version,
                "source": state.design_system.source,
                "css_variables": css_variables,
                "design_system": state.design_system.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    css_path.write_text(design_system_css(state.design_system, css_variables=css_variables), encoding="utf-8")

    return [
        WorkspaceFileRef(
            name=TOKEN_JSON_NAME,
            path=workspace_relative_path(json_path),
            description="Machine-readable Design token export derived from DesignSystemSpec.",
            source=state.production_session.capability,
        ),
        WorkspaceFileRef(
            name=TOKEN_CSS_NAME,
            path=workspace_relative_path(css_path),
            description="CSS custom properties derived from the approved Design system.",
            source=state.production_session.capability,
        ),
    ]


def design_system_css_variables(design_system: DesignSystemSpec) -> dict[str, str]:
    """Return CSS custom property names and values for a design system."""
    variables: dict[str, str] = {}
    for index, color in enumerate(design_system.colors, start=1):
        key = _css_var_name("color", color.name or f"color-{index}")
        _add_css_variable(variables, key, color.value)
    for index, typography in enumerate(design_system.typography, start=1):
        role = typography.role or f"type-{index}"
        _add_css_variable(variables, _css_var_name("font", role, "family"), typography.font_family)
        if typography.font_size_px:
            _add_css_variable(variables, _css_var_name("font", role, "size"), f"{typography.font_size_px}px")
        _add_css_variable(variables, _css_var_name("font", role, "weight"), typography.font_weight)
        _add_css_variable(variables, _css_var_name("font", role, "line-height"), typography.line_height)
    _add_group_variables(variables, "spacing", design_system.spacing)
    _add_group_variables(variables, "radius", design_system.radii)
    _add_group_variables(variables, "shadow", design_system.shadows)
    _add_nested_group_variables(variables, "component", design_system.component_tokens)
    return variables


def design_system_css(design_system: DesignSystemSpec, *, css_variables: dict[str, str] | None = None) -> str:
    """Render CSS custom properties for a design system."""
    variables = css_variables or design_system_css_variables(design_system)
    lines = [
        "/* CreativeClaw Design token export.",
        f"   Source: {design_system.source}; Design system: {design_system.design_system_id}. */",
        ":root {",
    ]
    for name, value in sorted(variables.items()):
        lines.append(f"  {name}: {_css_value(value)};")
    lines.append("}")
    return "\n".join(lines).rstrip() + "\n"


def _add_group_variables(variables: dict[str, str], group: str, values: dict[str, str]) -> None:
    for key, value in values.items():
        _add_css_variable(variables, _css_var_name(group, key), value)


def _add_nested_group_variables(variables: dict[str, str], group: str, values: dict[str, Any]) -> None:
    for key, value in _flatten_scalar_values(values):
        _add_css_variable(variables, _css_var_name(group, *key), value)


def _flatten_scalar_values(values: dict[str, Any], prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    flattened: list[tuple[tuple[str, ...], str]] = []
    for key, value in values.items():
        next_prefix = (*prefix, str(key))
        if isinstance(value, dict):
            flattened.extend(_flatten_scalar_values(value, next_prefix))
        elif isinstance(value, (str, int, float, bool)):
            flattened.append((next_prefix, str(value)))
    return flattened


def _add_css_variable(variables: dict[str, str], key: str, value: Any) -> None:
    text = str(value or "").strip()
    if text:
        variables[key] = text


def _css_var_name(*parts: str) -> str:
    slug = "-".join(_slug(part) for part in parts if str(part).strip())
    return f"--cc-{slug or 'token'}"


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text or "token"


def _css_value(value: str) -> str:
    return str(value).replace("\n", " ").replace(";", "").strip()
