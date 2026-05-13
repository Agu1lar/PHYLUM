from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from action_models import ActionEffects, ActionIssue, ActionResult
from office_agent import OfficeAgent, OfficeComUnavailable
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class OfficeInput(BaseModel):
    action: str = Field(
        ...,
        pattern="^(open_document|export_pdf|save_as_document|list_workbook_sheets|word_find_text|excel_read_range|outlook_search_messages|draft_email_with_attachment|reveal_active_document_path)$",
    )
    path: Optional[str] = None
    output_path: Optional[str] = None
    app_name: Optional[str] = None
    to: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    attachment_path: Optional[str] = None
    query: Optional[str] = None
    sheet_name: Optional[str] = None
    range_address: Optional[str] = None
    limit: Optional[int] = None


class OfficeTool(BaseTool):
    InputModel = OfficeInput
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 120, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.agent = OfficeAgent()

    async def validate(self, payload: OfficeInput) -> None:
        if payload.action in {"open_document", "export_pdf", "save_as_document", "list_workbook_sheets"} and not payload.path:
            raise ValueError(f"{payload.action} requires path")
        if payload.action == "word_find_text" and (not payload.path or not payload.query):
            raise ValueError("word_find_text requires path and query")
        if payload.action == "excel_read_range" and not payload.path:
            raise ValueError("excel_read_range requires path")
        if payload.action == "outlook_search_messages" and not payload.query:
            raise ValueError("outlook_search_messages requires query")
        if payload.action == "save_as_document" and not payload.output_path:
            raise ValueError("save_as_document requires output_path")
        if payload.action == "reveal_active_document_path" and not payload.app_name:
            raise ValueError("reveal_active_document_path requires app_name")

    async def _run(self, payload: OfficeInput) -> ActionResult:
        target = {
            key: value
            for key, value in {
                "path": payload.path,
                "output_path": payload.output_path,
                "app_name": payload.app_name,
                "to": payload.to,
                "attachment_path": payload.attachment_path,
                "query": payload.query,
                "sheet_name": payload.sheet_name,
                "range_address": payload.range_address,
            }.items()
            if value is not None
        }
        try:
            if payload.action == "open_document":
                details = await self.agent.open_document(payload.path or "")
                summary = f"Abri o documento do Office {payload.path}."
                changed = False
            elif payload.action == "export_pdf":
                details = await self.agent.export_pdf(payload.path or "", payload.output_path)
                summary = f"Exportei {payload.path} para PDF."
                changed = True
            elif payload.action == "save_as_document":
                details = await self.agent.save_as_document(payload.path or "", payload.output_path or "")
                summary = f"Salvei uma nova copia de {payload.path}."
                changed = True
            elif payload.action == "list_workbook_sheets":
                details = await self.agent.list_workbook_sheets(payload.path or "")
                summary = f"Listei as planilhas do workbook {payload.path}."
                changed = False
            elif payload.action == "word_find_text":
                details = await self.agent.word_find_text(payload.path or "", payload.query or "", limit=payload.limit or 20)
                summary = f"Busquei texto no documento Word {payload.path}."
                changed = False
            elif payload.action == "excel_read_range":
                details = await self.agent.excel_read_range(payload.path or "", sheet_name=payload.sheet_name, range_address=payload.range_address or "A1:Z50")
                summary = f"Li um intervalo do workbook {payload.path}."
                changed = False
            elif payload.action == "outlook_search_messages":
                details = await self.agent.outlook_search_messages(payload.query or "", limit=payload.limit or 25)
                summary = f"Busquei mensagens no Outlook por {payload.query}."
                changed = False
            elif payload.action == "draft_email_with_attachment":
                details = await self.agent.draft_email_with_attachment(
                    to=payload.to,
                    subject=payload.subject,
                    body=payload.body,
                    attachment_path=payload.attachment_path,
                )
                summary = "Criei um rascunho de email no Outlook."
                changed = True
            elif payload.action == "reveal_active_document_path":
                details = await self.agent.reveal_active_document_path(payload.app_name or "")
                summary = f"Descobri o documento ativo em {payload.app_name}."
                changed = False
            else:
                raise ValueError(f"unsupported office action: {payload.action}")
            return ActionResult(
                status="succeeded",
                summary=summary,
                tool="office",
                action=payload.action,
                semantic_type="mutation" if changed else "inspection",
                target=target,
                data=details,
                effects=ActionEffects(changed=changed),
                diagnostics={"backend": "office_com"},
            )
        except OfficeComUnavailable as exc:
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="office",
                action=payload.action,
                semantic_type="inspection",
                target=target,
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="office_com_unavailable", message=str(exc), retryable=False),
                diagnostics={"backend": "office_com"},
            )
        except Exception as exc:
            logger.exception("office action failed")
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="office",
                action=payload.action,
                semantic_type="inspection",
                target=target,
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="tool_internal", message=str(exc), retryable=False),
                diagnostics={"backend": "office_com"},
            )

