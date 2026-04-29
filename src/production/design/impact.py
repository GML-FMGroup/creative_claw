"""Read-only revision impact analysis for Design production."""

from __future__ import annotations

from typing import Any

from src.production.design.models import DesignProductionState
from src.production.models import new_id


_BRIEF_KEYWORDS = (
    "copy",
    "copywriting",
    "message",
    "messaging",
    "headline",
    "heading",
    "audience",
    "文案",
    "标题",
    "卖点",
    "表达",
    "受众",
)
_DESIGN_SYSTEM_KEYWORDS = (
    "color",
    "font",
    "style",
    "palette",
    "theme",
    "mood",
    "vibe",
    "brand",
    "branding",
    "tone",
    "颜色",
    "字体",
    "风格",
    "色调",
    "配色",
    "美术",
    "调性",
    "品牌",
    "视觉",
)
_ALIASES_BY_TERM = {
    "hero": ("首屏", "头图", "主视觉", "开屏", "第一屏"),
    "home": ("首页", "主页"),
    "index": ("首页", "主页"),
    "pricing": ("价格", "定价", "付费", "套餐"),
    "price": ("价格", "定价", "付费", "套餐"),
    "about": ("关于", "团队", "公司"),
    "contact": ("联系", "咨询"),
    "nav": ("导航", "菜单", "页头"),
    "navigation": ("导航", "菜单", "页头"),
    "navbar": ("导航", "菜单", "页头"),
    "header": ("页头", "导航", "菜单"),
    "footer": ("页脚", "底部"),
    "feature": ("功能", "特性", "卖点"),
    "features": ("功能", "特性", "卖点"),
    "product": ("产品", "商品"),
    "testimonial": ("评价", "客户证言", "口碑"),
    "testimonials": ("评价", "客户证言", "口碑"),
    "faq": ("常见问题", "问答"),
    "cta": ("行动按钮", "转化", "按钮"),
}


def normalize_revision_request(user_response: Any | None) -> dict[str, Any]:
    """Normalize free-form or structured revision input into one dictionary."""
    if isinstance(user_response, dict):
        normalized = dict(user_response)
        normalized.setdefault("notes", "")
        normalized.setdefault("targets", [])
        return normalized
    text = str(user_response or "").strip()
    return {"notes": text, "targets": []}


def build_revision_impact_view(state: DesignProductionState, user_response: Any | None) -> dict[str, Any]:
    """Return a read-only impact view for a requested design revision."""
    request = normalize_revision_request(user_response)
    notes = str(request.get("notes") or "").lower()
    targets = list(request.get("targets") or [])
    affected_section_ids = _affected_sections_from_request(state, notes=notes, targets=targets)
    explicitly_targeted_page_ids = _affected_pages_from_targets(state, targets=targets)
    note_targeted_page_ids = _affected_pages_from_notes(state, notes=notes)
    scoped_page_ids = explicitly_targeted_page_ids or note_targeted_page_ids
    if scoped_page_ids:
        allowed_section_ids = set(_section_ids_for_pages(state, scoped_page_ids))
        affected_section_ids = [section_id for section_id in affected_section_ids if section_id in allowed_section_ids]
    affected_page_ids = _affected_page_ids(
        state,
        notes=notes,
        affected_section_ids=affected_section_ids,
        explicitly_targeted_page_ids=scoped_page_ids,
    )
    generic_change = not affected_section_ids and not explicitly_targeted_page_ids and not _notes_match_page(state, notes)
    affected_artifact_ids = [
        artifact.artifact_id
        for artifact in state.html_artifacts
        if generic_change or artifact.page_id in set(affected_page_ids)
    ]
    return {
        "view_type": "revision_impact",
        "revision_id": new_id("design_revision"),
        "revision_request": request,
        "state_mutation": "none",
        "summary": "Confirmed design revisions rebuild affected HTML page artifacts while preserving unaffected active pages.",
        "affected_brief": generic_change or _notes_contain_any(notes, _BRIEF_KEYWORDS),
        "affected_design_system": generic_change or _notes_contain_any(notes, _DESIGN_SYSTEM_KEYWORDS),
        "affected_page_ids": affected_page_ids,
        "affected_section_ids": affected_section_ids,
        "affected_asset_ids": [asset.asset_id for asset in state.reference_assets if generic_change],
        "affected_artifact_ids": affected_artifact_ids,
        "recommended_action": "rebuild_page",
        "available_targets": _available_targets(state),
    }


def _affected_sections_from_request(
    state: DesignProductionState,
    *,
    notes: str,
    targets: list[Any],
) -> list[str]:
    explicit_ids = {
        str(target.get("id") or "").strip()
        for target in targets
        if isinstance(target, dict) and str(target.get("id") or "").strip()
    }
    result: list[str] = []
    if state.layout_plan is None:
        return result
    for page in state.layout_plan.pages:
        for section in page.sections:
            section_terms = _terms_for_target(section.section_id, section.title)
            if section.section_id in explicit_ids or _notes_contain_any(notes, section_terms):
                result.append(section.section_id)
    return result


def _section_ids_for_pages(state: DesignProductionState, page_ids: list[str]) -> list[str]:
    selected_page_ids = set(page_ids)
    if state.layout_plan is None:
        return []
    return [
        section.section_id
        for page in state.layout_plan.pages
        if page.page_id in selected_page_ids
        for section in page.sections
    ]


def _affected_pages_from_targets(
    state: DesignProductionState,
    *,
    targets: list[Any],
) -> list[str]:
    explicit_ids = [
        str(target.get("id") or "").strip()
        for target in targets
        if isinstance(target, dict) and str(target.get("id") or "").strip()
    ]
    if not explicit_ids:
        return []
    page_ids: list[str] = []
    if state.layout_plan is not None:
        known_page_ids = {page.page_id for page in state.layout_plan.pages}
        for target_id in explicit_ids:
            if target_id in known_page_ids and target_id not in page_ids:
                page_ids.append(target_id)
    for artifact in state.html_artifacts:
        if artifact.artifact_id in explicit_ids and artifact.page_id not in page_ids:
            page_ids.append(artifact.page_id)
    return page_ids


def _affected_pages_from_notes(state: DesignProductionState, *, notes: str) -> list[str]:
    """Return page ids whose title or path is directly named in revision notes."""
    pages = state.layout_plan.pages if state.layout_plan is not None else []
    return [
        page.page_id
        for page in pages
        if _page_matches_notes(page_title=page.title, page_path=page.path, notes=notes)
    ]


def _affected_page_ids(
    state: DesignProductionState,
    *,
    notes: str,
    affected_section_ids: list[str],
    explicitly_targeted_page_ids: list[str],
) -> list[str]:
    pages = state.layout_plan.pages if state.layout_plan is not None else []
    if not pages:
        return []
    explicit = set(explicitly_targeted_page_ids)
    if explicit:
        return [page.page_id for page in pages if page.page_id in explicit]
    affected_sections = set(affected_section_ids)
    matched_page_ids = {
        page.page_id
        for page in pages
        if page.page_id in explicit
        or any(section.section_id in affected_sections for section in page.sections)
        or _page_matches_notes(page_title=page.title, page_path=page.path, notes=notes)
    }
    if matched_page_ids:
        return [page.page_id for page in pages if page.page_id in matched_page_ids]
    return [page.page_id for page in pages]


def _notes_match_page(state: DesignProductionState, notes: str) -> bool:
    pages = state.layout_plan.pages if state.layout_plan is not None else []
    return any(_page_matches_notes(page_title=page.title, page_path=page.path, notes=notes) for page in pages)


def _page_matches_notes(*, page_title: str, page_path: str, notes: str) -> bool:
    if not notes:
        return False
    return _notes_contain_any(notes, _terms_for_target(page_title, page_path))


def _terms_for_target(*values: str) -> list[str]:
    """Return matching terms and common aliases for a page or section target."""
    terms: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if not text:
            continue
        normalized = text.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")
        candidates = [text, normalized, *normalized.split()]
        for candidate in candidates:
            cleaned = candidate.strip()
            if len(cleaned) < 2 or cleaned in terms:
                continue
            terms.append(cleaned)
            for alias in _ALIASES_BY_TERM.get(cleaned, ()):
                if alias not in terms:
                    terms.append(alias)
    return terms


def _notes_contain_any(notes: str, terms: tuple[str, ...] | list[str]) -> bool:
    """Return whether any term appears in revision notes."""
    return any(term and term.lower() in notes for term in terms)


def _available_targets(state: DesignProductionState) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    if state.layout_plan is not None:
        for page in state.layout_plan.pages:
            targets.append({"kind": "page", "id": page.page_id, "label": page.title})
            for section in page.sections:
                targets.append({"kind": "section", "id": section.section_id, "label": section.title})
    for artifact in state.html_artifacts:
        targets.append({"kind": "html_artifact", "id": artifact.artifact_id, "label": artifact.path})
    return targets
