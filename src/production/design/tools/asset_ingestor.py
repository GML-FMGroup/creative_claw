"""Reference asset ingestion for Design production sessions."""

from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path
from typing import Any

from src.production.design.models import ReferenceAssetEntry
from src.production.models import new_id
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


class AssetIngestor:
    """Copy or register user-provided design reference assets."""

    def ingest(
        self,
        *,
        session_root: Path,
        input_files: list[dict[str, Any]],
        turn_index: int,
    ) -> list[ReferenceAssetEntry]:
        """Copy workspace input files into the design session asset directory."""
        assets_dir = session_root / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        entries: list[ReferenceAssetEntry] = []
        for item in input_files:
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            try:
                source_path = resolve_workspace_path(raw_path)
                asset_id = new_id("design_asset")
                target_name = f"{asset_id}_{Path(name or source_path.name).name}"
                target_path = assets_dir / target_name
                if source_path.resolve() != target_path.resolve():
                    shutil.copy2(source_path, target_path)
                relative_target = workspace_relative_path(target_path)
                entries.append(
                    ReferenceAssetEntry(
                        asset_id=asset_id,
                        kind=_infer_asset_kind(source_path, description=description),
                        path=relative_target,
                        name=name or source_path.name,
                        description=description,
                        added_turn_index=turn_index,
                    )
                )
            except Exception as exc:
                entries.append(
                    ReferenceAssetEntry(
                        kind="other",
                        name=name or Path(raw_path).name,
                        status="failed",
                        stale_reason=f"Failed to ingest asset: {type(exc).__name__}",
                        description=description,
                        added_turn_index=turn_index,
                    )
                )
        return entries


def _infer_asset_kind(path: Path, *, description: str) -> str:
    """Infer a design asset kind from file metadata and user description."""
    lowered = f"{path.name} {description}".lower()
    mime_type, _ = mimetypes.guess_type(str(path))
    if "logo" in lowered or "brand" in lowered:
        return "logo"
    if "screenshot" in lowered or "screen" in lowered:
        return "screenshot"
    if "product" in lowered or "商品" in lowered or "产品" in lowered:
        return "product_photo"
    if path.suffix.lower() in {".css"}:
        return "css_token"
    if path.suffix.lower() in {".ttf", ".otf", ".woff", ".woff2"}:
        return "font_file"
    if mime_type and mime_type.startswith("image/"):
        return "reference_image"
    return "other"

