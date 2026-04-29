import asyncio
import json
import os
import struct
import unittest
import zlib
from html.parser import HTMLParser
from pathlib import Path

from src.production.design.expert_runtime import DesignExpertRuntime
from src.production.design.manager import DesignProductionManager
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path, workspace_root


def _adk_state(sid: str = "session_design_real_smoke") -> dict:
    return {
        "sid": sid,
        "turn_index": 1,
        "step": 0,
        "channel": "cli",
        "chat_id": "terminal",
        "sender_id": "cli-user",
        "uploaded": [],
        "generated": [],
        "files_history": [],
        "final_file_paths": [],
    }


class _SmokeHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image_srcs: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_by_name = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "img":
            self.image_srcs.append(attrs_by_name.get("src", ""))
        if tag.lower() == "a":
            self.links.append(attrs_by_name.get("href", ""))


def _write_smoke_logo(path: Path) -> None:
    """Write a small generated PNG logo with visible brand colors."""
    width = 96
    height = 40
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            if x < 22 and 8 <= y < 32:
                color = (10, 112, 92)
            elif 32 <= x < 86 and 11 <= y < 18:
                color = (8, 28, 45)
            elif 32 <= x < 70 and 23 <= y < 29:
                color = (230, 181, 43)
            else:
                color = (250, 250, 247)
            row.extend(color)
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", checksum)

    png = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(b"".join(rows))),
            chunk(b"IEND", b""),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _normalized_html_links(links: list[str]) -> set[str]:
    normalized: set[str] = set()
    for href in links:
        if not href or href.startswith("#") or "://" in href:
            continue
        path = href.split("#", 1)[0].split("?", 1)[0].lstrip("./")
        if path:
            normalized.add(path)
    return normalized


@unittest.skipUnless(
    os.environ.get("CREATIVE_CLAW_RUN_DESIGN_REAL_SMOKE") == "1",
    "Set CREATIVE_CLAW_RUN_DESIGN_REAL_SMOKE=1 to run live Design LLM and Playwright smoke tests.",
)
class DesignRealSmokeTest(unittest.TestCase):
    def test_real_model_browser_smoke_covers_assets_and_multipage_chrome(self) -> None:
        logo_path = workspace_root() / "test_inputs" / "design_real_smoke" / "smoke-logo.png"
        _write_smoke_logo(logo_path)
        state = _adk_state()
        model_reference = os.environ.get("CREATIVE_CLAW_DESIGN_REAL_SMOKE_MODEL") or None
        manager = DesignProductionManager(
            expert_runtime=DesignExpertRuntime(model_reference=model_reference),
        )

        started = asyncio.run(
            manager.start(
                user_prompt=(
                    "Design a two-page microsite for Northstar Ledger, a compliance analytics product. "
                    "Use the uploaded logo image visibly in the shared header on every page, align colors to it, "
                    "keep the site fully offline, and use identical header and footer chrome across pages."
                ),
                input_files=[
                    {
                        "path": workspace_relative_path(logo_path),
                        "name": "smoke-logo.png",
                        "description": "Northstar Ledger logo with teal, ink, and gold brand colors.",
                    }
                ],
                placeholder_design=False,
                design_settings={
                    "design_genre": "micro_site",
                    "build_mode": "multi_html",
                    "pages": [
                        {"title": "Home", "path": "index.html"},
                        {"title": "Product", "path": "product.html"},
                    ],
                },
                adk_state=state,
            )
        )
        self.assertEqual(started.status, "needs_user_review")
        self.assertEqual(started.stage, "design_direction_review")

        preview = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(preview.status, "needs_user_review")
        self.assertEqual(preview.stage, "preview_review")
        persisted = resolve_workspace_path(preview.state_ref or "")
        payload = json.loads(persisted.read_text(encoding="utf-8"))
        active_artifacts = [item for item in payload["html_artifacts"] if item["status"] == "valid"]
        self.assertEqual(len(active_artifacts), 2)
        self.assertTrue(any(item["metadata"].get("shared_html_context_used") for item in active_artifacts[1:]))

        for artifact in active_artifacts:
            html_path = resolve_workspace_path(artifact["path"])
            html = html_path.read_text(encoding="utf-8")
            parser = _SmokeHtmlParser()
            parser.feed(html)
            self.assertNotIn(str(workspace_root()), html)
            self.assertTrue(
                any("../assets/" in src for src in parser.image_srcs),
                f"{artifact['path']} does not use session asset srcs: {parser.image_srcs}",
            )
            bad_asset_srcs = [
                src
                for src in parser.image_srcs
                if src.startswith(("assets/", "/assets/", "generated/")) or src.startswith("file:")
            ]
            self.assertFalse(bad_asset_srcs, f"{artifact['path']} has non-portable asset srcs: {bad_asset_srcs}")
            links = _normalized_html_links(parser.links)
            self.assertIn("index.html", links)
            self.assertIn("product.html", links)

        invalid_previews = [report for report in payload["preview_reports"] if not report["valid"]]
        self.assertFalse(invalid_previews, f"Preview rendering must be real and valid: {invalid_previews}")
        network_failures = [failure for report in payload["preview_reports"] for failure in report["network_failures"]]
        self.assertFalse(network_failures, f"Smoke HTML should not depend on failed network resources: {network_failures}")
        screenshot_paths = [report["screenshot_path"] for report in payload["preview_reports"]]
        self.assertGreaterEqual(len(screenshot_paths), 4)
        for screenshot_path in screenshot_paths:
            self.assertTrue(resolve_workspace_path(screenshot_path).exists(), screenshot_path)


if __name__ == "__main__":
    unittest.main()
