"""Source-document extraction for PPT production."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from src.production.ppt.models import DocumentSummary, IngestEntry
from src.runtime.workspace import resolve_workspace_path


_MAX_TEXT_CHARS_PER_DOCUMENT = 12_000
_MAX_FACTS = 8
_MAX_SUMMARY_CHARS = 900
_WORD_TEXT_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentLoaderService:
    """Build deterministic summaries from PPT source-document inputs."""

    def build_summary(self, inputs: list[IngestEntry]) -> DocumentSummary:
        """Extract source-document text and return a bounded summary."""
        docs = [item for item in inputs if item.role == "source_doc" and item.status == "valid"]
        if not docs:
            return DocumentSummary(status="not_started")

        warnings: list[str] = []
        extracted: list[tuple[IngestEntry, str]] = []
        documents_metadata: list[dict[str, object]] = []
        metadata: dict[str, object] = {"documents": documents_metadata}
        for entry in docs:
            text, entry_warnings = self._extract_entry(entry)
            warnings.extend(entry_warnings)
            document_info = {
                "input_id": entry.input_id,
                "name": entry.name,
                "characters": len(text),
                "status": "ready" if text else "unsupported",
            }
            documents_metadata.append(document_info)
            if text:
                extracted.append((entry, text[:_MAX_TEXT_CHARS_PER_DOCUMENT]))

        if not extracted:
            return DocumentSummary(
                source_input_ids=[item.input_id for item in docs],
                summary="No supported source-document text could be extracted yet.",
                status="unsupported",
                warnings=warnings or ["No supported source document text was extracted."],
                document_count=len(docs),
                metadata=metadata,
            )

        combined_text = "\n\n".join(text for _, text in extracted)
        facts = _salient_facts(combined_text)
        return DocumentSummary(
            source_input_ids=[item.input_id for item, _ in extracted],
            summary=_summary_for(extracted, combined_text),
            salient_facts=facts,
            status="ready",
            warnings=warnings,
            document_count=len(extracted),
            extracted_character_count=sum(len(text) for _, text in extracted),
            metadata=metadata,
        )

    def _extract_entry(self, entry: IngestEntry) -> tuple[str, list[str]]:
        path = resolve_workspace_path(entry.path)
        suffix = Path(entry.path).suffix.lower()
        if not path.is_file():
            return "", [f"Source document `{entry.name}` was not found in the workspace."]
        try:
            if suffix in {".txt", ".md"}:
                return _read_text(path), []
            if suffix == ".docx":
                return _read_docx_text(path), []
            if suffix == ".pdf":
                return "", ["PDF extraction is not enabled in P1a; attach TXT, MD, or DOCX for source-aware outlines."]
        except Exception as exc:
            return "", [f"Failed to extract `{entry.name}`: {type(exc).__name__}: {exc}"]
        return "", [f"Unsupported source document type for `{entry.name}`."]


def _read_text(path: Path) -> str:
    """Read plain text with a forgiving UTF-8 fallback."""
    try:
        return _normalize_text(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return _normalize_text(path.read_text(encoding="utf-8", errors="replace"))


def _read_docx_text(path: Path) -> str:
    """Extract visible paragraph text from a DOCX OOXML package."""
    with zipfile.ZipFile(path) as package:
        try:
            document_xml = package.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("DOCX package does not contain word/document.xml") from exc
    root = ElementTree.fromstring(document_xml)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_WORD_TEXT_NS}p"):
        parts = [node.text or "" for node in paragraph.iter(f"{_WORD_TEXT_NS}t")]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return _normalize_text("\n".join(paragraphs))


def _summary_for(extracted: list[tuple[IngestEntry, str]], combined_text: str) -> str:
    names = ", ".join(entry.name for entry, _ in extracted[:3])
    if len(extracted) > 3:
        names = f"{names}, +{len(extracted) - 3} more"
    first_sentence = _first_sentence(combined_text)
    summary = f"Loaded {len(extracted)} source document(s): {names}."
    if first_sentence:
        summary = f"{summary} Main signal: {first_sentence}"
    return summary[:_MAX_SUMMARY_CHARS].rstrip()


def _salient_facts(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for line in re.split(r"[\n。！？!?]+", text):
        normalized = re.sub(r"\s+", " ", line).strip(" -•\t")
        dedupe_key = normalized.lower()
        if len(normalized) < 12 or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(normalized[:220])
        if len(candidates) >= _MAX_FACTS:
            break
    return candidates


def _first_sentence(text: str) -> str:
    facts = _salient_facts(text)
    return facts[0] if facts else ""


def _normalize_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    return "\n".join(line for line in lines if line).strip()
