# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import asyncio
import email
import hashlib
import json
import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
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

try:  # pragma: no cover - optional dependency
    import fitz
except Exception:  # pragma: no cover - optional dependency
    fitz = None

try:  # pragma: no cover - optional dependency
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None

try:  # pragma: no cover - optional dependency
    import pytesseract
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None

DOCUMENT_INDEX_PATH = Path(__file__).resolve().parent / "agent_workspace" / "document_index.json"


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
    SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".json", ".eml", ".msg", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

    def __init__(self, *, index_path: Optional[Path] = None):
        self.index_path = index_path or DOCUMENT_INDEX_PATH

    def _load_index(self) -> Dict[str, Any]:
        try:
            if self.index_path.exists():
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            logger.exception("failed to load document index")
        return {"documents": {}}

    def _save_index(self, index: Dict[str, Any]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(index, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")

    def _file_id(self, path: Path) -> str:
        stat = path.stat()
        raw = f"{path.resolve()}::{stat.st_size}::{stat.st_mtime}"
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _tokenize(self, text: str) -> List[str]:
        return [token.lower() for token in re.findall(r"[\wÀ-ÿ]{3,}", text or "")]

    def _semantic_terms(self, query: str) -> List[str]:
        haystack = query.lower()
        expansions = {
            "contract": ["contrato", "contract", "acordo", "agreement", "clausula", "clause", "assinatura"],
            "invoice": ["nota", "fiscal", "nfe", "invoice", "fatura", "danfe", "cnpj", "boleto", "valor"],
            "email": ["email", "mensagem", "subject", "assunto", "remetente", "from"],
            "attachment": ["anexo", "attachment", "arquivo", "filename"],
        }
        terms = set(self._tokenize(query))
        for kind, kind_terms in expansions.items():
            if kind in haystack or any(term in haystack for term in kind_terms):
                terms.update(kind_terms)
        return sorted(terms)

    def _origin_for(self, path: Path, root: Optional[Path] = None) -> str:
        raw = str(path)
        if raw.startswith("\\\\"):
            parts = raw.strip("\\").split("\\")
            return f"share:{parts[0]}\\{parts[1]}" if len(parts) >= 2 else "share"
        if root:
            return str(root)
        return path.anchor or "local"

    def _basic_metadata(self, path: Path, *, root: Optional[Path] = None) -> Dict[str, Any]:
        stat = path.stat()
        return {
            "path": str(path),
            "name": path.name,
            "extension": path.suffix.lower(),
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
            "modified_date": datetime.fromtimestamp(stat.st_mtime).date().isoformat(),
            "origin": self._origin_for(path, root=root),
            "author": None,
            "created_at": None,
        }

    def _extract_pdf_metadata(self, path: Path) -> Dict[str, Any]:
        if PdfReader is None:
            return {}
        try:
            reader = PdfReader(str(path))
            metadata = reader.metadata or {}
            return {
                "author": metadata.get("/Author") or metadata.get("author"),
                "created_at": metadata.get("/CreationDate") or metadata.get("creation_date"),
                "title": metadata.get("/Title") or metadata.get("title"),
                "page_count": len(reader.pages),
            }
        except Exception:
            logger.exception("failed to extract PDF metadata %s", path)
            return {}

    def _extract_office_core_metadata(self, path: Path) -> Dict[str, Any]:
        try:
            with zipfile.ZipFile(path) as archive:
                if "docProps/core.xml" not in archive.namelist():
                    return {}
                root = ElementTree.fromstring(archive.read("docProps/core.xml"))
                values = {node.tag.rsplit("}", 1)[-1]: node.text for node in root.iter() if node.text}
                return {
                    "author": values.get("creator") or values.get("lastModifiedBy"),
                    "created_at": values.get("created"),
                    "title": values.get("title"),
                }
        except Exception:
            logger.exception("failed to extract Office metadata %s", path)
            return {}

    def _extract_pdf(self, path: Path) -> str:
        if PdfReader is None:
            return ""
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    def _ocr_image(self, path: Path) -> Dict[str, Any]:
        if Image is None or pytesseract is None:
            return {"text": "", "ocr_used": False, "ocr_available": False, "ocr_engine": None}
        try:
            text = pytesseract.image_to_string(Image.open(path))
            return {"text": text or "", "ocr_used": bool((text or "").strip()), "ocr_available": True, "ocr_engine": "tesseract"}
        except Exception as exc:
            logger.exception("OCR failed for image %s", path)
            return {"text": "", "ocr_used": False, "ocr_available": True, "ocr_engine": "tesseract", "ocr_error": str(exc)}

    def _ocr_pdf(self, path: Path) -> Dict[str, Any]:
        if fitz is None or Image is None or pytesseract is None:
            return {"text": "", "ocr_used": False, "ocr_available": False, "ocr_engine": None}
        chunks: List[str] = []
        try:
            with fitz.open(str(path)) as document:
                for page in document:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    chunks.append(pytesseract.image_to_string(image) or "")
            text = "\n".join(chunk for chunk in chunks if chunk)
            return {"text": text, "ocr_used": bool(text.strip()), "ocr_available": True, "ocr_engine": "tesseract+pymupdf"}
        except Exception as exc:
            logger.exception("OCR failed for PDF %s", path)
            return {"text": "", "ocr_used": False, "ocr_available": True, "ocr_engine": "tesseract+pymupdf", "ocr_error": str(exc)}

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

    def _extract_eml_metadata(self, path: Path) -> Dict[str, Any]:
        message = email.message_from_bytes(path.read_bytes())
        attachments = []
        for part in message.walk():
            filename = part.get_filename()
            if filename:
                attachments.append({"filename": filename, "content_type": part.get_content_type()})
        if not attachments:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            for filename in re.findall(r'filename="?([^"\r\n;]+)"?', raw, flags=re.IGNORECASE):
                attachments.append({"filename": filename, "content_type": "unknown"})
        return {
            "author": message.get("from"),
            "created_at": message.get("date"),
            "title": message.get("subject"),
            "to": message.get("to"),
            "attachments": attachments,
        }

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

    def _extract_msg_metadata(self, path: Path) -> Dict[str, Any]:
        if extract_msg is None:
            return {}
        message = extract_msg.Message(str(path))
        attachments = [{"filename": getattr(item, "longFilename", None) or getattr(item, "shortFilename", None)} for item in (message.attachments or [])]
        return {
            "author": message.sender or None,
            "created_at": str(message.date) if message.date else None,
            "title": message.subject or None,
            "to": message.to or None,
            "attachments": [item for item in attachments if item.get("filename")],
        }

    def _extract_plain_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore")

    def _metadata_sync(self, path: Path, *, root: Optional[Path] = None) -> Dict[str, Any]:
        metadata = self._basic_metadata(path, root=root)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            metadata.update({key: value for key, value in self._extract_pdf_metadata(path).items() if value not in (None, "")})
        elif suffix in {".docx", ".xlsx", ".pptx"}:
            metadata.update({key: value for key, value in self._extract_office_core_metadata(path).items() if value not in (None, "")})
        elif suffix == ".eml":
            metadata.update({key: value for key, value in self._extract_eml_metadata(path).items() if value not in (None, "", [])})
        elif suffix == ".msg":
            metadata.update({key: value for key, value in self._extract_msg_metadata(path).items() if value not in (None, "", [])})
        return metadata

    def _classify_document(self, path: Path, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        haystack = f"{path.name}\n{metadata.get('title') or ''}\n{text}".lower()
        scores = {
            "contract": sum(1 for term in ["contrato", "contract", "acordo", "agreement", "clausula", "clause"] if term in haystack),
            "invoice": sum(1 for term in ["nota fiscal", "nfe", "nf-e", "invoice", "fatura", "danfe", "cnpj", "valor total"] if term in haystack),
            "email": 2 if path.suffix.lower() in {".eml", ".msg"} else sum(1 for term in ["from:", "subject:", "assunto", "anexo"] if term in haystack),
            "attachment": len(metadata.get("attachments") or []),
        }
        best_kind = max(scores, key=scores.get)
        best_score = scores[best_kind]
        return {"kind": best_kind if best_score > 0 else "document", "confidence": min(1.0, best_score / 4.0), "signals": scores}

    def _extract_text_sync(self, path: Path, *, use_ocr: bool = True, root: Optional[Path] = None) -> Dict[str, Any]:
        suffix = path.suffix.lower()
        ocr_details = {"ocr_used": False, "ocr_available": False, "ocr_engine": None}
        if suffix == ".pdf":
            text = self._extract_pdf(path)
            if use_ocr and len(text.strip()) < 20:
                ocr_details = self._ocr_pdf(path)
                text = ocr_details.get("text") or text
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
        elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
            ocr_details = self._ocr_image(path) if use_ocr else ocr_details
            text = ocr_details.get("text") or ""
        else:
            text = self._extract_plain_text(path)
        metadata = self._metadata_sync(path, root=root)
        classification = self._classify_document(path, text, metadata)
        return {
            "path": str(path),
            "extension": suffix,
            "text": text,
            "metadata": metadata,
            "classification": classification,
            **ocr_details,
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
            "metadata": await asyncio.to_thread(self._metadata_sync, path_obj),
        }

    async def extract_text(self, path: str, *, use_ocr: bool = True) -> Dict[str, Any]:
        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(path)
        return await asyncio.to_thread(self._extract_text_sync, path_obj, use_ocr=use_ocr)

    def _iter_supported_files(self, root_path: Path) -> Iterable[Path]:
        for candidate in root_path.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                yield candidate

    def _passes_filters(self, record: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> bool:
        if not filters:
            return True
        metadata = record.get("metadata") or {}
        extension = filters.get("extension")
        if extension:
            allowed = {item.lower() if str(item).startswith(".") else f".{str(item).lower()}" for item in (extension if isinstance(extension, list) else [extension])}
            if record.get("extension") not in allowed:
                return False
        author = filters.get("author")
        if author and str(author).lower() not in str(metadata.get("author") or "").lower():
            return False
        origin = filters.get("origin")
        if origin and str(origin).lower() not in str(metadata.get("origin") or "").lower():
            return False
        kind = filters.get("kind")
        if kind and str(kind).lower() != str((record.get("classification") or {}).get("kind") or "").lower():
            return False
        modified_after = filters.get("modified_after")
        if modified_after:
            try:
                if float(metadata.get("modified_at") or 0) < float(modified_after):
                    return False
            except Exception:
                pass
        modified_before = filters.get("modified_before")
        if modified_before:
            try:
                if float(metadata.get("modified_at") or 0) > float(modified_before):
                    return False
            except Exception:
                pass
        return True

    async def search_content(self, root: str, query: str, limit: int = 25, *, filters: Optional[Dict[str, Any]] = None, use_ocr: bool = True) -> Dict[str, Any]:
        root_path = Path(root)
        if not root_path.exists():
            raise FileNotFoundError(root)
        lowered_query = query.lower()
        matches: List[Dict[str, Any]] = []
        for candidate in self._iter_supported_files(root_path):
            try:
                extracted = await asyncio.to_thread(self._extract_text_sync, candidate, use_ocr=use_ocr, root=root_path)
                if not self._passes_filters(extracted, filters):
                    continue
                haystack = extracted.get("text", "").lower()
                if lowered_query in haystack:
                    excerpt_match = re.search(re.escape(query), extracted.get("text", ""), re.IGNORECASE)
                    excerpt = extracted.get("text", "")[max((excerpt_match.start() if excerpt_match else 0) - 80, 0): (excerpt_match.end() if excerpt_match else 0) + 160]
                    matches.append(
                        {
                            "path": extracted["path"],
                            "extension": extracted["extension"],
                            "metadata": extracted["metadata"],
                            "classification": extracted["classification"],
                            "ocr_used": extracted.get("ocr_used", False),
                            "excerpt": excerpt.strip(),
                        }
                    )
            except Exception:
                logger.exception("failed to inspect document %s", candidate)
            if len(matches) >= limit:
                break
        return {"matches": matches}

    async def index_documents(self, root: str, limit: int = 500, *, filters: Optional[Dict[str, Any]] = None, use_ocr: bool = True) -> Dict[str, Any]:
        root_path = Path(root)
        if not root_path.exists():
            raise FileNotFoundError(root)

        def _run() -> Dict[str, Any]:
            index = self._load_index()
            documents = index.setdefault("documents", {})
            indexed = 0
            skipped = 0
            for candidate in self._iter_supported_files(root_path):
                if indexed >= limit:
                    break
                try:
                    extracted = self._extract_text_sync(candidate, use_ocr=use_ocr, root=root_path)
                    if not self._passes_filters(extracted, filters):
                        skipped += 1
                        continue
                    tokens = self._tokenize(extracted.get("text", ""))
                    token_counts: Dict[str, int] = {}
                    for token in tokens:
                        token_counts[token] = token_counts.get(token, 0) + 1
                    file_id = self._file_id(candidate)
                    documents[file_id] = {
                        "file_id": file_id,
                        "path": extracted["path"],
                        "extension": extracted["extension"],
                        "metadata": extracted["metadata"],
                        "classification": extracted["classification"],
                        "token_counts": token_counts,
                        "text_preview": extracted.get("text", "")[:1200],
                        "ocr_used": extracted.get("ocr_used", False),
                        "indexed_at": time_now(),
                    }
                    indexed += 1
                except Exception:
                    skipped += 1
                    logger.exception("failed to index document %s", candidate)
            index["updated_at"] = time_now()
            index["root"] = str(root_path)
            self._save_index(index)
            return {"indexed": indexed, "skipped": skipped, "index_path": str(self.index_path), "total_documents": len(documents)}

        return await asyncio.to_thread(_run)

    async def search_index(self, query: str, limit: int = 25, *, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        index = await asyncio.to_thread(self._load_index)
        query_tokens = self._semantic_terms(query)
        matches: List[Dict[str, Any]] = []
        for record in (index.get("documents") or {}).values():
            if not self._passes_filters(record, filters):
                continue
            token_counts = record.get("token_counts") or {}
            score = sum(token_counts.get(token, 0) for token in query_tokens)
            preview = record.get("text_preview") or ""
            if query.lower() in preview.lower():
                score += 5
            if score <= 0:
                continue
            matches.append(
                {
                    "path": record.get("path"),
                    "extension": record.get("extension"),
                    "metadata": record.get("metadata"),
                    "classification": record.get("classification"),
                    "score": score,
                    "excerpt": preview[:500],
                    "ocr_used": record.get("ocr_used", False),
                }
            )
        matches.sort(key=lambda item: item["score"], reverse=True)
        return {"matches": matches[:limit], "index_path": str(self.index_path), "query_tokens": query_tokens}

    async def discover_documents(self, root: str, query: Optional[str] = None, limit: int = 50, *, filters: Optional[Dict[str, Any]] = None, use_ocr: bool = True) -> Dict[str, Any]:
        root_path = Path(root)
        if not root_path.exists():
            raise FileNotFoundError(root)
        matches: List[Dict[str, Any]] = []
        for candidate in self._iter_supported_files(root_path):
            try:
                extracted = await asyncio.to_thread(self._extract_text_sync, candidate, use_ocr=use_ocr, root=root_path)
                if not self._passes_filters(extracted, filters):
                    continue
                haystack = f"{candidate.name}\n{(extracted.get('metadata') or {}).get('title') or ''}\n{extracted.get('text') or ''}".lower()
                query_score = 1 if not query else sum(1 for token in self._tokenize(query) if token in haystack)
                classification = extracted.get("classification") or {}
                confidence = float(classification.get("confidence") or 0)
                if query_score > 0 or confidence > 0:
                    matches.append(
                        {
                            "path": extracted["path"],
                            "extension": extracted["extension"],
                            "metadata": extracted["metadata"],
                            "classification": classification,
                            "score": query_score + confidence,
                            "ocr_used": extracted.get("ocr_used", False),
                            "excerpt": (extracted.get("text") or "")[:500],
                        }
                    )
            except Exception:
                logger.exception("failed to discover document %s", candidate)
            if len(matches) >= limit:
                break
        matches.sort(key=lambda item: item["score"], reverse=True)
        return {"matches": matches[:limit]}

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


def time_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

