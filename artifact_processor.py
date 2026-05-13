"""Artifact Processor: internal file loading, transformation and analysis.

Allows the agent to load a file into memory, process it (extract text,
transform data, analyze content) and return the result without requiring
the user's machine to do the heavy lifting or leaving intermediate files.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_TEXT_OUTPUT = 512_000


class ArtifactResult:
    __slots__ = ("ok", "data", "summary", "artifact_type", "metadata", "error")

    def __init__(
        self,
        *,
        ok: bool,
        data: Any = None,
        summary: str = "",
        artifact_type: str = "unknown",
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ):
        self.ok = ok
        self.data = data
        self.summary = summary
        self.artifact_type = artifact_type
        self.metadata = metadata or {}
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        data = self.data
        if isinstance(data, str) and len(data) > MAX_TEXT_OUTPUT:
            data = data[:MAX_TEXT_OUTPUT] + "\n... (truncated)"
        return {
            "ok": self.ok,
            "data": data,
            "summary": self.summary,
            "artifact_type": self.artifact_type,
            "metadata": self.metadata,
            "error": self.error,
        }


class ArtifactProcessor:
    SUPPORTED_TEXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".log", ".ini", ".cfg", ".yaml", ".yml", ".toml", ".py", ".js", ".ts", ".ps1", ".bat", ".cmd", ".sh"}
    SUPPORTED_BINARY = {".pdf", ".docx", ".xlsx", ".pptx", ".msg"}

    async def load_and_read(self, path: str) -> ArtifactResult:
        file_path = Path(path)
        if not file_path.exists():
            return ArtifactResult(ok=False, error=f"File not found: {path}")
        if not file_path.is_file():
            return ArtifactResult(ok=False, error=f"Not a file: {path}")
        size = file_path.stat().st_size
        if size > MAX_FILE_SIZE:
            return ArtifactResult(ok=False, error=f"File too large ({size} bytes, max {MAX_FILE_SIZE})")

        suffix = file_path.suffix.lower()
        metadata = {"path": str(file_path), "size": size, "extension": suffix}

        if suffix == ".csv" or suffix == ".tsv":
            return await self._read_csv(file_path, metadata, delimiter="\t" if suffix == ".tsv" else ",")
        if suffix == ".json":
            return await self._read_json(file_path, metadata)
        if suffix == ".pdf":
            return await self._read_pdf(file_path, metadata)
        if suffix in {".docx", ".xlsx", ".pptx"}:
            return await self._read_office_xml(file_path, metadata)
        if suffix == ".msg":
            return await self._read_msg(file_path, metadata)
        if suffix in self.SUPPORTED_TEXT:
            return await self._read_text(file_path, metadata)

        return await self._read_text(file_path, metadata)

    async def transform(self, path: str, operation: str, params: Optional[Dict[str, Any]] = None) -> ArtifactResult:
        params = params or {}
        loaded = await self.load_and_read(path)
        if not loaded.ok:
            return loaded

        if operation == "summarize":
            return self._summarize(loaded)
        if operation == "extract_table":
            return self._extract_table(loaded, params)
        if operation == "filter_lines":
            return self._filter_lines(loaded, params)
        if operation == "convert_json":
            return self._convert_to_json(loaded)
        if operation == "stats":
            return self._compute_stats(loaded)

        return ArtifactResult(ok=False, error=f"Unsupported operation: {operation}")

    async def write_result(self, content: str, output_path: str, *, encoding: str = "utf-8") -> ArtifactResult:
        try:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding=encoding)
            return ArtifactResult(
                ok=True,
                summary=f"Written {len(content)} chars to {output_path}",
                artifact_type="written_file",
                metadata={"path": str(out), "size": len(content)},
            )
        except Exception as exc:
            return ArtifactResult(ok=False, error=str(exc))

    async def _read_text(self, path: Path, metadata: Dict[str, Any]) -> ArtifactResult:
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            lines = text.count("\n") + 1
            metadata["lines"] = lines
            return ArtifactResult(
                ok=True,
                data=text,
                summary=f"Text file with {lines} lines, {len(text)} chars",
                artifact_type="text",
                metadata=metadata,
            )
        except Exception as exc:
            return ArtifactResult(ok=False, error=str(exc), metadata=metadata)

    async def _read_csv(self, path: Path, metadata: Dict[str, Any], delimiter: str = ",") -> ArtifactResult:
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            rows = list(reader)
            headers = reader.fieldnames or []
            metadata["rows"] = len(rows)
            metadata["columns"] = headers
            return ArtifactResult(
                ok=True,
                data={"headers": headers, "rows": rows[:500], "total_rows": len(rows)},
                summary=f"CSV with {len(rows)} rows and {len(headers)} columns",
                artifact_type="tabular",
                metadata=metadata,
            )
        except Exception as exc:
            return ArtifactResult(ok=False, error=str(exc), metadata=metadata)

    async def _read_json(self, path: Path, metadata: Dict[str, Any]) -> ArtifactResult:
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
            data = json.loads(text)
            kind = "array" if isinstance(data, list) else "object"
            count = len(data) if isinstance(data, (list, dict)) else 1
            metadata["json_type"] = kind
            metadata["element_count"] = count
            return ArtifactResult(
                ok=True,
                data=data,
                summary=f"JSON {kind} with {count} elements",
                artifact_type="json",
                metadata=metadata,
            )
        except Exception as exc:
            return ArtifactResult(ok=False, error=str(exc), metadata=metadata)

    async def _read_pdf(self, path: Path, metadata: Dict[str, Any]) -> ArtifactResult:
        try:
            import pypdf
        except ImportError:
            try:
                import fitz
                doc = fitz.open(str(path))
                pages_text: List[str] = []
                for page in doc:
                    pages_text.append(page.get_text())
                doc.close()
                text = "\n\n".join(pages_text)
                metadata["pages"] = len(pages_text)
                return ArtifactResult(
                    ok=True,
                    data=text,
                    summary=f"PDF with {len(pages_text)} pages, {len(text)} chars",
                    artifact_type="pdf_text",
                    metadata=metadata,
                )
            except ImportError:
                return ArtifactResult(ok=False, error="Neither pypdf nor pymupdf installed for PDF reading", metadata=metadata)

        reader = pypdf.PdfReader(str(path))
        pages_text = []
        for page in reader.pages:
            pages_text.append(page.extract_text() or "")
        text = "\n\n".join(pages_text)
        metadata["pages"] = len(pages_text)
        return ArtifactResult(
            ok=True,
            data=text,
            summary=f"PDF with {len(pages_text)} pages, {len(text)} chars",
            artifact_type="pdf_text",
            metadata=metadata,
        )

    async def _read_office_xml(self, path: Path, metadata: Dict[str, Any]) -> ArtifactResult:
        suffix = path.suffix.lower()
        if suffix == ".docx":
            return await self._read_docx(path, metadata)
        if suffix == ".xlsx":
            return await self._read_xlsx(path, metadata)
        return ArtifactResult(ok=False, error=f"Office format {suffix} not yet supported for internal processing", metadata=metadata)

    async def _read_docx(self, path: Path, metadata: Dict[str, Any]) -> ArtifactResult:
        try:
            from zipfile import ZipFile
            import xml.etree.ElementTree as ET

            NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            with ZipFile(str(path)) as zf:
                xml_content = zf.read("word/document.xml")
            root = ET.fromstring(xml_content)
            paragraphs = []
            for p in root.iter(f"{NS}p"):
                texts = [t.text for t in p.iter(f"{NS}t") if t.text]
                if texts:
                    paragraphs.append("".join(texts))
            text = "\n".join(paragraphs)
            metadata["paragraphs"] = len(paragraphs)
            return ArtifactResult(
                ok=True,
                data=text,
                summary=f"DOCX with {len(paragraphs)} paragraphs, {len(text)} chars",
                artifact_type="docx_text",
                metadata=metadata,
            )
        except Exception as exc:
            return ArtifactResult(ok=False, error=str(exc), metadata=metadata)

    async def _read_xlsx(self, path: Path, metadata: Dict[str, Any]) -> ArtifactResult:
        try:
            from zipfile import ZipFile
            import xml.etree.ElementTree as ET

            NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            with ZipFile(str(path)) as zf:
                shared_strings: List[str] = []
                if "xl/sharedStrings.xml" in zf.namelist():
                    ss_xml = zf.read("xl/sharedStrings.xml")
                    ss_root = ET.fromstring(ss_xml)
                    for si in ss_root.iter(f"{NS}si"):
                        texts = [t.text or "" for t in si.iter(f"{NS}t")]
                        shared_strings.append("".join(texts))

                sheet_xml = zf.read("xl/worksheets/sheet1.xml")
                sheet_root = ET.fromstring(sheet_xml)
                rows_data: List[List[str]] = []
                for row in sheet_root.iter(f"{NS}row"):
                    cells: List[str] = []
                    for c in row.iter(f"{NS}c"):
                        v_el = c.find(f"{NS}v")
                        val = v_el.text if v_el is not None else ""
                        if c.get("t") == "s" and val and val.isdigit():
                            idx = int(val)
                            val = shared_strings[idx] if idx < len(shared_strings) else val
                        cells.append(val or "")
                    rows_data.append(cells)

            metadata["rows"] = len(rows_data)
            return ArtifactResult(
                ok=True,
                data={"rows": rows_data[:500], "total_rows": len(rows_data)},
                summary=f"XLSX sheet1 with {len(rows_data)} rows",
                artifact_type="xlsx_data",
                metadata=metadata,
            )
        except Exception as exc:
            return ArtifactResult(ok=False, error=str(exc), metadata=metadata)

    async def _read_msg(self, path: Path, metadata: Dict[str, Any]) -> ArtifactResult:
        try:
            import extract_msg
            msg = extract_msg.Message(str(path))
            data = {
                "subject": msg.subject,
                "sender": msg.sender,
                "to": msg.to,
                "date": str(msg.date) if msg.date else None,
                "body": msg.body,
                "attachments": [a.longFilename or a.shortFilename for a in (msg.attachments or [])],
            }
            msg.close()
            return ArtifactResult(
                ok=True,
                data=data,
                summary=f"MSG email: '{data['subject']}' from {data['sender']}",
                artifact_type="email",
                metadata=metadata,
            )
        except ImportError:
            return ArtifactResult(ok=False, error="extract_msg not installed for .msg reading", metadata=metadata)
        except Exception as exc:
            return ArtifactResult(ok=False, error=str(exc), metadata=metadata)

    def _summarize(self, result: ArtifactResult) -> ArtifactResult:
        data = result.data
        if isinstance(data, str):
            lines = data.strip().splitlines()
            preview = "\n".join(lines[:30])
            return ArtifactResult(
                ok=True,
                data=preview,
                summary=f"First 30 lines of {result.artifact_type} ({len(lines)} total lines)",
                artifact_type="summary",
                metadata=result.metadata,
            )
        if isinstance(data, dict) and "rows" in data:
            rows = data.get("rows", [])
            return ArtifactResult(
                ok=True,
                data={"preview": rows[:10], "total": data.get("total_rows", len(rows))},
                summary=f"First 10 rows of {data.get('total_rows', len(rows))} total",
                artifact_type="summary",
                metadata=result.metadata,
            )
        return ArtifactResult(ok=True, data=str(data)[:2000], summary="Summarized content", artifact_type="summary", metadata=result.metadata)

    def _filter_lines(self, result: ArtifactResult, params: Dict[str, Any]) -> ArtifactResult:
        pattern = params.get("pattern", "")
        if not isinstance(result.data, str):
            return ArtifactResult(ok=False, error="filter_lines only works on text content")
        lines = result.data.splitlines()
        matched = [line for line in lines if pattern.lower() in line.lower()]
        return ArtifactResult(
            ok=True,
            data="\n".join(matched),
            summary=f"Filtered {len(matched)} of {len(lines)} lines matching '{pattern}'",
            artifact_type="filtered_text",
            metadata=result.metadata,
        )

    def _extract_table(self, result: ArtifactResult, params: Dict[str, Any]) -> ArtifactResult:
        if isinstance(result.data, dict) and "rows" in result.data:
            return result
        if isinstance(result.data, str):
            lines = result.data.strip().splitlines()
            rows: List[List[str]] = []
            for line in lines:
                if "\t" in line:
                    rows.append(line.split("\t"))
                elif "," in line:
                    rows.append(line.split(","))
            if rows:
                return ArtifactResult(
                    ok=True,
                    data={"rows": rows[:500], "total_rows": len(rows)},
                    summary=f"Extracted {len(rows)} rows from text",
                    artifact_type="tabular",
                    metadata=result.metadata,
                )
        return ArtifactResult(ok=False, error="Could not extract table from this content")

    def _convert_to_json(self, result: ArtifactResult) -> ArtifactResult:
        data = result.data
        if isinstance(data, (dict, list)):
            return ArtifactResult(
                ok=True,
                data=json.dumps(data, ensure_ascii=False, indent=2, default=str),
                summary="Converted to JSON",
                artifact_type="json",
                metadata=result.metadata,
            )
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
                return ArtifactResult(ok=True, data=json.dumps(parsed, ensure_ascii=False, indent=2), summary="Parsed as JSON", artifact_type="json", metadata=result.metadata)
            except json.JSONDecodeError:
                pass
        return ArtifactResult(ok=False, error="Content cannot be converted to JSON")

    def _compute_stats(self, result: ArtifactResult) -> ArtifactResult:
        data = result.data
        stats: Dict[str, Any] = {"artifact_type": result.artifact_type}
        if isinstance(data, str):
            stats["chars"] = len(data)
            stats["lines"] = data.count("\n") + 1
            stats["words"] = len(data.split())
        elif isinstance(data, dict):
            if "rows" in data:
                stats["total_rows"] = data.get("total_rows", len(data["rows"]))
                if data["rows"]:
                    stats["columns"] = len(data["rows"][0]) if isinstance(data["rows"][0], list) else list(data["rows"][0].keys()) if isinstance(data["rows"][0], dict) else None
            stats["keys"] = list(data.keys())
        elif isinstance(data, list):
            stats["items"] = len(data)

        stats.update(result.metadata)
        return ArtifactResult(
            ok=True,
            data=stats,
            summary=f"Stats for {result.artifact_type}",
            artifact_type="stats",
            metadata=result.metadata,
        )
