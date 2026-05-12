from __future__ import annotations

import asyncio
import email
import logging
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None

try:  # pragma: no cover - optional dependency
    import extract_msg
except Exception:  # pragma: no cover - optional dependency
    extract_msg = None


def _strip_xml_text(xml_text: bytes) -> str:
    try:
        root = ElementTree.fromstring(xml_text)
    except Exception:
        return ""
    chunks: List[str] = []
    for node in root.iter():
        if node.text and node.text.strip():
            chunks.append(node.text.strip())
    return "\n".join(chunks)


class DocumentIntelligenceAgent:
    SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".json", ".eml", ".msg"}

    def _extract_pdf(self, path: Path) -> str:
        if PdfReader is None:
            return ""
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    def _extract_docx(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            return _strip_xml_text(archive.read("word/document.xml"))

    def _extract_pptx(self, path: Path) -> str:
        chunks: List[str] = []
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                    chunks.append(_strip_xml_text(archive.read(name)))
        return "\n".join(chunk for chunk in chunks if chunk)

    def _extract_xlsx(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            chunks: List[str] = []
            for name in archive.namelist():
                if name.startswith("xl/sharedStrings") and name.endswith(".xml"):
                    chunks.append(_strip_xml_text(archive.read(name)))
            return "\n".join(chunk for chunk in chunks if chunk)

    def _extract_eml(self, path: Path) -> str:
        message = email.message_from_bytes(path.read_bytes())
        parts: List[str] = [message.get("subject", ""), message.get("from", ""), message.get("to", "")]
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True) or b""
                    parts.append(payload.decode(errors="ignore"))
        else:
            payload = message.get_payload(decode=True) or b""
            parts.append(payload.decode(errors="ignore"))
        return "\n".join(item for item in parts if item)

    def _extract_msg(self, path: Path) -> str:
        if extract_msg is None:
            return ""
        message = extract_msg.Message(str(path))
        return "\n".join(
            item
            for item in [
                message.subject or "",
                message.sender or "",
                message.to or "",
                message.body or "",
            ]
            if item
        )

    def _extract_plain_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore")

    def _extract_text_sync(self, path: Path) -> Dict[str, Any]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text = self._extract_pdf(path)
        elif suffix == ".docx":
            text = self._extract_docx(path)
        elif suffix == ".pptx":
            text = self._extract_pptx(path)
        elif suffix == ".xlsx":
            text = self._extract_xlsx(path)
        elif suffix == ".eml":
            text = self._extract_eml(path)
        elif suffix == ".msg":
            text = self._extract_msg(path)
        else:
            text = self._extract_plain_text(path)
        return {
            "path": str(path),
            "extension": suffix,
            "text": text,
            "size": path.stat().st_size,
            "modified_at": path.stat().st_mtime,
        }

    async def inspect_document(self, path: str) -> Dict[str, Any]:
        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(path)
        suffix = path_obj.suffix.lower()
        return {
            "path": str(path_obj),
            "name": path_obj.name,
            "extension": suffix,
            "size": path_obj.stat().st_size,
            "modified_at": path_obj.stat().st_mtime,
            "supported": suffix in self.SUPPORTED_EXTENSIONS,
        }

    async def extract_text(self, path: str) -> Dict[str, Any]:
        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(path)
        return await asyncio.to_thread(self._extract_text_sync, path_obj)

    async def search_content(self, root: str, query: str, limit: int = 25) -> Dict[str, Any]:
        root_path = Path(root)
        if not root_path.exists():
            raise FileNotFoundError(root)
        lowered_query = query.lower()
        matches: List[Dict[str, Any]] = []
        for candidate in root_path.rglob("*"):
            try:
                if not candidate.is_file() or candidate.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                    continue
                extracted = await asyncio.to_thread(self._extract_text_sync, candidate)
                haystack = extracted.get("text", "").lower()
                if lowered_query in haystack:
                    excerpt_match = re.search(re.escape(query), extracted.get("text", ""), re.IGNORECASE)
                    excerpt = extracted.get("text", "")[max((excerpt_match.start() if excerpt_match else 0) - 80, 0): (excerpt_match.end() if excerpt_match else 0) + 160]
                    matches.append(
                        {
                            "path": extracted["path"],
                            "extension": extracted["extension"],
                            "excerpt": excerpt.strip(),
                        }
                    )
            except Exception:
                logger.exception("failed to inspect document %s", candidate)
            if len(matches) >= limit:
                break
        return {"matches": matches}

    async def recent_documents(self, query: Optional[str] = None, limit: int = 25) -> Dict[str, Any]:
        recent_dir = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Recent"
        if not recent_dir.exists():
            return {"documents": []}
        lowered_query = (query or "").lower()
        documents: List[Dict[str, Any]] = []
        for item in sorted(recent_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not item.is_file():
                continue
            if lowered_query and lowered_query not in item.name.lower():
                continue
            documents.append(
                {
                    "shortcut": str(item),
                    "name": item.name,
                    "modified_at": item.stat().st_mtime,
                }
            )
            if len(documents) >= limit:
                break
        return {"documents": documents}

