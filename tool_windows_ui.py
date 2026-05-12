from __future__ import annotations

import logging
from typing import Dict, Optional

from action_models import ActionEffects, ActionIssue, ActionResult
from tool_base import BaseTool
from windows_ui_agent import WindowsUiAgent, WindowsUiUnavailable
from windows_ui_models import WindowsUiRequest

logger = logging.getLogger(__name__)


class WindowsUiTool(BaseTool):
    InputModel = WindowsUiRequest
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.agent = WindowsUiAgent()

    async def validate(self, payload: WindowsUiRequest) -> None:
        if payload.action in {"inspect_window", "list_elements", "find_element", "wait_for_element", "invoke_element", "set_text", "select_item", "scroll", "read_element_text"}:
            if payload.hwnd is None and not payload.title and not payload.process_name:
                raise ValueError(f"{payload.action} requires hwnd, title or process_name")
        if payload.action in {"find_element", "wait_for_element", "invoke_element", "set_text", "select_item", "scroll", "read_element_text"} and not payload.selector and not payload.element_id:
            raise ValueError(f"{payload.action} requires selector or element_id")
        if payload.action == "set_text" and payload.text is None:
            raise ValueError("set_text requires text")
        if payload.action == "select_item" and payload.item_text is None:
            raise ValueError("select_item requires item_text")
        if payload.action == "send_hotkey" and payload.hotkey is None:
            raise ValueError("send_hotkey requires hotkey")

    def _target(self, payload: WindowsUiRequest) -> Dict[str, object]:
        return {
            key: value
            for key, value in {
                "hwnd": payload.hwnd,
                "title": payload.title,
                "process_name": payload.process_name,
                "selector": payload.selector,
                "element_id": payload.element_id,
            }.items()
            if value is not None
        }

    def _success_result(self, payload: WindowsUiRequest, summary: str, data: Dict[str, object]) -> ActionResult:
        return ActionResult(
            status="succeeded",
            summary=summary,
            tool="windows_ui",
            action=payload.action,
            semantic_type="mutation" if payload.action in {"invoke_element", "set_text", "select_item", "send_hotkey", "scroll"} else "inspection",
            target=self._target(payload),
            data=data,
            effects=ActionEffects(changed=payload.action in {"invoke_element", "set_text", "select_item", "send_hotkey", "scroll"}),
            diagnostics={"backend": "pywinauto"},
        )

    async def _run(self, payload: WindowsUiRequest) -> ActionResult:
        try:
            if payload.action == "inspect_window":
                details = await self.agent.inspect_window(
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    include_children=payload.include_children,
                    max_results=payload.max_results or 25,
                )
                return self._success_result(payload, "Inspecionei a janela solicitada.", details)
            if payload.action == "list_elements":
                details = await self.agent.list_elements(
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    selector=payload.selector,
                    include_children=payload.include_children,
                    max_results=payload.max_results or 50,
                )
                return self._success_result(payload, f"Listei {len(details.get('elements') or [])} elemento(s).", details)
            if payload.action == "find_element":
                details = await self.agent.find_element(
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    selector=payload.selector,
                    element_id=payload.element_id,
                    include_children=payload.include_children,
                    max_results=payload.max_results or 10,
                )
                matches = details.get("matches") or []
                if not matches:
                    return ActionResult(
                        status="failed",
                        summary="Nao encontrei nenhum elemento que combine com o seletor informado.",
                        tool="windows_ui",
                        action=payload.action,
                        semantic_type="inspection",
                        target=self._target(payload),
                        data=details,
                        effects=ActionEffects(changed=False),
                        issue=ActionIssue(kind="element_not_found", message="No matching UI element was found."),
                        diagnostics={"backend": "pywinauto"},
                    )
                if len(matches) > 1:
                    return ActionResult(
                        status="needs_input",
                        summary="Encontrei mais de um elemento possivel para este seletor.",
                        tool="windows_ui",
                        action=payload.action,
                        semantic_type="inspection",
                        target=self._target(payload),
                        data={"matches": matches},
                        effects=ActionEffects(changed=False),
                        issue=ActionIssue(
                            kind="ambiguous_match",
                            message="Multiple UI elements matched the selector.",
                            user_action_required="select_candidate",
                            candidates=matches,
                        ),
                        diagnostics={"backend": "pywinauto"},
                    )
                return self._success_result(payload, "Elemento encontrado.", {"element": matches[0], "matches": matches})
            if payload.action == "wait_for_element":
                details = await self.agent.wait_for_element(
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    selector=payload.selector,
                    element_id=payload.element_id,
                    include_children=payload.include_children,
                    timeout_seconds=payload.timeout_seconds or 15,
                )
                return self._success_result(payload, "Elemento encontrado apos espera.", details)
            if payload.action == "invoke_element":
                details = await self.agent.invoke_element(
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    selector=payload.selector,
                    element_id=payload.element_id,
                )
                return self._success_result(payload, "Acionei o elemento nativo.", details)
            if payload.action == "set_text":
                details = await self.agent.set_text(
                    text=payload.text or "",
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    selector=payload.selector,
                    element_id=payload.element_id,
                )
                return self._success_result(payload, "Preenchi o campo nativo solicitado.", details)
            if payload.action == "select_item":
                details = await self.agent.select_item(
                    item_text=payload.item_text or "",
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    selector=payload.selector,
                    element_id=payload.element_id,
                )
                return self._success_result(payload, "Selecionei o item solicitado.", details)
            if payload.action == "send_hotkey":
                details = await self.agent.send_hotkey(payload.hotkey or "")
                return self._success_result(payload, "Enviei o atalho de teclado.", details)
            if payload.action == "scroll":
                details = await self.agent.scroll(
                    direction=payload.direction,
                    amount=payload.amount or 1,
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    selector=payload.selector,
                    element_id=payload.element_id,
                )
                return self._success_result(payload, "Rolei o elemento solicitado.", details)
            if payload.action == "read_element_text":
                details = await self.agent.read_element_text(
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    selector=payload.selector,
                    element_id=payload.element_id,
                )
                return self._success_result(payload, "Li o texto do elemento solicitado.", details)
            if payload.action == "get_focused_element":
                details = await self.agent.get_focused_element()
                return self._success_result(payload, "Capturei o elemento com foco no momento.", details)
            raise ValueError(f"unsupported windows_ui action: {payload.action}")
        except WindowsUiUnavailable as exc:
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="windows_ui",
                action=payload.action,
                semantic_type="inspection",
                target=self._target(payload),
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="uia_unavailable", message=str(exc), retryable=False),
                diagnostics={"backend": "pywinauto"},
            )
        except TimeoutError as exc:
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="windows_ui",
                action=payload.action,
                semantic_type="inspection",
                target=self._target(payload),
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="timeout", message=str(exc), retryable=True),
                diagnostics={"backend": "pywinauto"},
            )
        except Exception as exc:
            logger.exception("windows_ui action failed")
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="windows_ui",
                action=payload.action,
                semantic_type="mutation" if payload.action in {"invoke_element", "set_text", "select_item", "send_hotkey", "scroll"} else "inspection",
                target=self._target(payload),
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="tool_internal", message=str(exc), retryable=False),
                diagnostics={"backend": "pywinauto"},
            )

