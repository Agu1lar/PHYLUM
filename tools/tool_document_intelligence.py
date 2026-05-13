# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from action_models import ActionEffects, ActionIssue, ActionResult
from document_intelligence_agent import DocumentIntelligenceAgent
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class DocumentIntelligenceInput(BaseModel):
    action: str = Field(..., pattern="^(inspect_document|extract_text|search_content|index_documents|search_index|discover_documents|recent_documents)$")
    path: Optional[str] = None
    root: Optional[str] = None
    query: Optional[str] = None
    limit: Optional[int] = None
    filters: Optional[Dict[str, Any]] = None
    use_ocr: bool = True


class DocumentIntelligenceTool(BaseTool):
    InputModel = DocumentIntelligenceInput
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 120, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.agent = DocumentIntelligenceAgent()

    async def validate(self, payload: DocumentIntelligenceInput) -> None:
        if payload.action in {"inspect_document", "extract_text"} and not payload.path:
            raise ValueError(f"{payload.action} requires path")
        if payload.action == "search_content" and (not payload.root or not payload.query):
            raise ValueError("search_content requires root and query")
        if payload.action in {"index_documents", "discover_documents"} and not payload.root:
            raise ValueError(f"{payload.action} requires root")
        if payload.action == "search_index" and not payload.query:
            raise ValueError("search_index requires query")

    async def _run(self, payload: DocumentIntelligenceInput) -> ActionResult:
        target = {
            key: value
            for key, value in {"path": payload.path, "root": payload.root, "query": payload.query}.items()
            if value is not None
        }
        try:
            if payload.action == "inspect_document":
                details = await self.agent.inspect_document(payload.path or "")
                summary = f"Inspecionei o documento {payload.path}."
            elif payload.action == "extract_text":
                details = await self.agent.extract_text(payload.path or "", use_ocr=payload.use_ocr)
                summary = f"ExtraI texto de {payload.path}."
            elif payload.action == "search_content":
                details = await self.agent.search_content(payload.root or "", payload.query or "", limit=payload.limit or 25, filters=payload.filters, use_ocr=payload.use_ocr)
                summary = f"Encontrei {len(details.get('matches') or [])} documento(s) contendo o texto pesquisado."
            elif payload.action == "index_documents":
                details = await self.agent.index_documents(payload.root or "", limit=payload.limit or 500, filters=payload.filters, use_ocr=payload.use_ocr)
                summary = f"Indexei {details.get('indexed') or 0} documento(s) localmente."
            elif payload.action == "search_index":
                details = await self.agent.search_index(payload.query or "", limit=payload.limit or 25, filters=payload.filters)
                summary = f"Encontrei {len(details.get('matches') or [])} documento(s) no indice local."
            elif payload.action == "discover_documents":
                details = await self.agent.discover_documents(payload.root or "", payload.query, limit=payload.limit or 50, filters=payload.filters, use_ocr=payload.use_ocr)
                summary = f"Descobri {len(details.get('matches') or [])} documento(s) candidatos."
            elif payload.action == "recent_documents":
                details = await self.agent.recent_documents(payload.query, limit=payload.limit or 25)
                summary = f"Listei {len(details.get('documents') or [])} documento(s) recentes."
            else:
                raise ValueError(f"unsupported document_intelligence action: {payload.action}")
            return ActionResult(
                status="succeeded",
                summary=summary,
                tool="document_intelligence",
                action=payload.action,
                semantic_type="inspection",
                target=target,
                data=details,
                effects=ActionEffects(changed=False),
                diagnostics={
                    "content_search": payload.action in {"search_content", "search_index", "discover_documents"},
                    "ocr_requested": payload.use_ocr,
                    "filters": payload.filters or {},
                },
            )
        except Exception as exc:
            logger.exception("document intelligence failed")
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="document_intelligence",
                action=payload.action,
                semantic_type="inspection",
                target=target,
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="document_access_failed", message=str(exc), retryable=False),
                diagnostics={},
            )

