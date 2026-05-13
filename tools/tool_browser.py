# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from action_models import ActionEffects, ActionIssue, ActionResult
from browser_agent import BrowserAgent
from desktop_windows_agent import DesktopWindowsAgent
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class BrowserInput(BaseModel):
    action: str = Field(..., pattern='^(open_page|search|download|scrape_structured|interact_dom|upload_file|bridge_native_dialog)$')
    url: Optional[str] = None
    base_url: Optional[str] = None
    query_selector: Optional[str] = None
    query: Optional[str] = None
    result_selector: Optional[str] = None
    extractors: Optional[Dict[str, str]] = None
    actions: Optional[List[Dict[str, Any]]] = None
    selector: Optional[str] = None
    file_path: Optional[str] = None
    headless: bool = True
    browser: str = Field('chromium')
    timeout: Optional[int] = Field(None, gt=0)
    title: Optional[str] = None
    process_name: Optional[str] = None


class BrowserTool(BaseTool):
    InputModel = BrowserInput
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 60, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.agent = BrowserAgent()
        self.desktop_agent = DesktopWindowsAgent()

    async def validate(self, payload: BrowserInput) -> None:
        if payload.action in {"open_page", "download", "scrape_structured"} and not payload.url:
            raise ValueError("url is required")
        if payload.action == "search":
            if not payload.base_url or not payload.query or not payload.query_selector:
                raise ValueError("search requires base_url, query and query_selector")
        if payload.action == "scrape_structured" and not payload.extractors:
            raise ValueError("extractors are required for scrape_structured")
        if payload.action == "interact_dom" and (not payload.url or not payload.actions):
            raise ValueError("interact_dom requires url and actions")
        if payload.action == "upload_file" and (not payload.url or not payload.selector or not payload.file_path):
            raise ValueError("upload_file requires url, selector and file_path")
        if payload.action == "bridge_native_dialog" and not payload.title and not payload.process_name:
            raise ValueError("bridge_native_dialog requires title or process_name")

    async def _run(self, payload: BrowserInput) -> ActionResult:
        timeout = payload.timeout or self.default_timeout
        if payload.action == "open_page":
            response = await self.agent.open_page(
                payload.url,
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
                retries=self.default_retries,
            )
        elif payload.action == "search":
            response = await self.agent.search(
                payload.base_url,
                payload.query_selector,
                payload.query,
                result_selector=payload.result_selector,
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        elif payload.action == "download":
            response = await self.agent.download(
                payload.url,
                link_selector=payload.selector,
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        elif payload.action == "scrape_structured":
            response = await self.agent.scrape_structured(
                payload.url,
                payload.extractors or {},
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        elif payload.action == "interact_dom":
            response = await self.agent.interact_dom(
                payload.url,
                payload.actions or [],
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        elif payload.action == "upload_file":
            response = await self.agent.upload_file(
                payload.url,
                payload.selector,
                payload.file_path,
                browser=payload.browser,
                headless=payload.headless,
                timeout=timeout,
            )
        elif payload.action == "bridge_native_dialog":
            windows = await self.desktop_agent.list_windows()
            candidates = [
                item
                for item in (windows.get("windows") or [])
                if (
                    (not payload.title or payload.title.lower() in str(item.get("title") or "").lower())
                    and (not payload.process_name or payload.process_name.lower() in str(item.get("process_name") or "").lower())
                )
            ]
            success = bool(candidates)
            target = {
                key: value
                for key, value in {"title": payload.title, "process_name": payload.process_name}.items()
                if value is not None
            }
            data = {"windows": candidates}
            if success:
                return ActionResult(
                    status="succeeded",
                    summary="Encontrei janelas nativas relacionadas ao fluxo do navegador.",
                    tool="browser",
                    action=payload.action,
                    semantic_type="inspection",
                    target=target,
                    data=data,
                    effects=ActionEffects(changed=False),
                    diagnostics={"bridge": "native_dialog"},
                )
            return ActionResult(
                status="failed",
                summary="Nao encontrei uma janela nativa correspondente ao dialogo do navegador.",
                tool="browser",
                action=payload.action,
                semantic_type="inspection",
                target=target,
                data=data,
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="browser_native_bridge_required", message="No matching native dialog window was found.", retryable=True),
                diagnostics={"bridge": "native_dialog"},
            )
        else:
            raise ValueError(f"unsupported browser action: {payload.action}")

        details = response.dict()
        success = bool(details.get("ok"))
        error = details.pop("error", None)
        target = {
            key: value
            for key, value in {
                "url": payload.url,
                "base_url": payload.base_url,
                "query": payload.query,
                "selector": payload.selector,
                "file_path": payload.file_path,
                "title": payload.title,
                "process_name": payload.process_name,
            }.items()
            if value is not None
        }
        data = {
            key: value
            for key, value in {
                "url": details.get("url"),
                "title": details.get("title"),
                "content_snippet": details.get("content_snippet"),
                "downloads": details.get("downloads"),
                "console": details.get("console"),
                "screenshot_path": details.get("screenshot_path"),
            }.items()
            if value is not None
        }
        if payload.action == "scrape_structured" and details.get("raw"):
            data["raw"] = details.get("raw")
        if success:
            summary = {
                "open_page": f"Abri a pagina {details.get('url') or payload.url}.",
                "search": f"Executei a busca em {payload.base_url}.",
                "download": f"Conclui a tentativa de download a partir de {payload.url}.",
                "scrape_structured": f"ExtraI dados estruturados de {payload.url}.",
                "interact_dom": f"Interagi com a pagina {payload.url}.",
                "upload_file": f"Enviei o arquivo {payload.file_path} para {payload.url}.",
            }.get(payload.action, f"A acao {payload.action} foi executada com sucesso.")
            return ActionResult(
                status="succeeded",
                summary=summary,
                tool="browser",
                action=payload.action,
                semantic_type="mutation" if payload.action in {"download", "interact_dom", "upload_file"} else "inspection",
                target=target,
                data=data,
                effects=ActionEffects(changed=payload.action in {"download", "interact_dom", "upload_file"}),
                diagnostics={"raw": details},
            )

        issue = ActionIssue(
            kind="browser_failed",
            message=error or f"Browser action {payload.action} failed.",
            retryable=False,
            details=details,
        )
        return ActionResult(
            status="failed",
            summary=issue.message,
            tool="browser",
            action=payload.action,
            semantic_type="mutation" if payload.action in {"download", "interact_dom", "upload_file"} else "inspection",
            target=target,
            data=data,
            effects=ActionEffects(changed=False),
            issue=issue,
            diagnostics={"raw": details},
        )
