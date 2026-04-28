"""Source-document extraction for PPT production."""

from __future__ import annotations

import re
import zlib
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
                text = _read_pdf_text(path)
                if text:
                    return text, []
                return "", [f"No extractable PDF text layer was found in `{entry.name}`. Attach TXT, MD, or DOCX for scanned or complex PDFs."]
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


def _read_pdf_text(path: Path) -> str:
    """Extract simple text-layer content from a PDF without optional dependencies."""
    data = path.read_bytes()
    if b"/Encrypt" in data[:2048]:
        raise ValueError("Encrypted PDF packages are not supported by lightweight extraction.")
    text_parts: list[str] = []
    for stream, stream_dict in _iter_pdf_streams(data):
        decoded = _decode_pdf_stream(stream, stream_dict)
        if not decoded:
            continue
        text_parts.extend(_extract_pdf_text_objects(decoded))
    return _normalize_text("\n".join(text_parts))


def _iter_pdf_streams(data: bytes):
    """Yield raw PDF content streams with their nearby stream dictionaries."""
    position = 0
    while True:
        stream_index = data.find(b"stream", position)
        if stream_index < 0:
            return
        stream_start = stream_index + len(b"stream")
        if data[stream_start:stream_start + 2] == b"\r\n":
            stream_start += 2
        elif data[stream_start:stream_start + 1] in {b"\n", b"\r"}:
            stream_start += 1
        stream_end = data.find(b"endstream", stream_start)
        if stream_end < 0:
            return
        stream = data[stream_start:stream_end].strip(b"\r\n")
        stream_dict = _stream_dictionary_before(data, stream_index)
        yield stream, stream_dict
        position = stream_end + len(b"endstream")


def _stream_dictionary_before(data: bytes, stream_index: int) -> bytes:
    dictionary_start = data.rfind(b"<<", 0, stream_index)
    dictionary_end = data.rfind(b">>", 0, stream_index)
    if dictionary_start < 0 or dictionary_end < dictionary_start:
        return b""
    return data[dictionary_start:dictionary_end + 2]


def _decode_pdf_stream(stream: bytes, stream_dict: bytes) -> str:
    payload = stream
    if b"/FlateDecode" in stream_dict or b"/Fl" in stream_dict:
        try:
            payload = zlib.decompress(stream)
        except zlib.error:
            return ""
    return payload.decode("latin-1", errors="ignore")


def _extract_pdf_text_objects(stream_text: str) -> list[str]:
    blocks = re.findall(r"BT\b(.*?)\bET", stream_text, flags=re.DOTALL)
    if not blocks:
        blocks = [stream_text]
    extracted: list[str] = []
    for block in blocks:
        tokens = _pdf_string_tokens(block)
        if tokens:
            extracted.append(" ".join(tokens))
    return extracted


def _pdf_string_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "(":
            value, index = _parse_pdf_literal_string(text, index)
            if _is_useful_pdf_text(value):
                tokens.append(value)
            continue
        if char == "<" and not text.startswith("<<", index):
            value, index = _parse_pdf_hex_string(text, index)
            if _is_useful_pdf_text(value):
                tokens.append(value)
            continue
        index += 1
    return tokens


def _parse_pdf_literal_string(text: str, start: int) -> tuple[str, int]:
    chars: list[str] = []
    depth = 1
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            value, index = _parse_pdf_escape(text, index)
            chars.append(value)
            continue
        if char == "(":
            depth += 1
            chars.append(char)
            index += 1
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                return _normalize_pdf_token("".join(chars)), index + 1
            chars.append(char)
            index += 1
            continue
        chars.append(char)
        index += 1
    return _normalize_pdf_token("".join(chars)), index


def _parse_pdf_escape(text: str, start: int) -> tuple[str, int]:
    index = start + 1
    if index >= len(text):
        return "", index
    char = text[index]
    mapped = {"n": "\n", "r": "\n", "t": "\t", "b": "", "f": "", "(": "(", ")": ")", "\\": "\\"}
    if char in mapped:
        return mapped[char], index + 1
    if char in "\r\n":
        while index < len(text) and text[index] in "\r\n":
            index += 1
        return "", index
    if char in "01234567":
        end = index
        while end < min(index + 3, len(text)) and text[end] in "01234567":
            end += 1
        try:
            return chr(int(text[index:end], 8)), end
        except ValueError:
            return "", end
    return char, index + 1


def _parse_pdf_hex_string(text: str, start: int) -> tuple[str, int]:
    end = text.find(">", start + 1)
    if end < 0:
        return "", len(text)
    raw = re.sub(r"\s+", "", text[start + 1:end])
    if len(raw) % 2:
        raw += "0"
    try:
        payload = bytes.fromhex(raw)
    except ValueError:
        return "", end + 1
    if payload.startswith(b"\xfe\xff"):
        value = payload[2:].decode("utf-16-be", errors="ignore")
    elif payload.startswith(b"\xff\xfe"):
        value = payload[2:].decode("utf-16-le", errors="ignore")
    else:
        value = payload.decode("latin-1", errors="ignore")
    return _normalize_pdf_token(value), end + 1


def _normalize_pdf_token(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\x00", "")).strip()


def _is_useful_pdf_text(value: str) -> bool:
    if len(value.strip()) < 3:
        return False
    alnum_count = sum(char.isalnum() for char in value)
    return alnum_count >= max(2, len(value) // 4)


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
