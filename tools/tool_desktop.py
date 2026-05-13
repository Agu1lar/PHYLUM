import logging
from typing import Optional

from action_models import ActionEffects, ActionIssue, ActionResult
from desktop_windows_agent import DesktopWindowsAgent
from desktop_windows_models import DesktopRequest
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class DesktopTool(BaseTool):
    InputModel = DesktopRequest
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.agent = DesktopWindowsAgent()

    async def validate(self, payload: DesktopRequest) -> None:
        if payload.action in {"open_path", "open_file"} and not payload.path:
            raise ValueError(f"{payload.action} requires path")
        if payload.action in {"explorer_select_path", "explorer_navigate", "inspect_installer"} and not payload.path:
            raise ValueError(f"{payload.action} requires path")
        if payload.action == "explorer_rename_path" and (not payload.path or not payload.new_name):
            raise ValueError("explorer_rename_path requires path and new_name")
        if payload.action in {"explorer_copy_path", "explorer_move_path"} and (not payload.path or not payload.dest):
            raise ValueError(f"{payload.action} requires path and dest")
        if payload.action == "open_app" and not payload.app_name and not payload.app_path:
            raise ValueError("open_app requires app_name or app_path")
        if payload.action == "get_explorer_selection":
            return
        if payload.action == "wait_for_window" and payload.hwnd is None and not payload.title and not payload.process_name:
            raise ValueError("wait_for_window requires hwnd, title or process_name")
        if payload.action == "focus_window" and payload.hwnd is None and not payload.title:
            raise ValueError("focus_window requires hwnd or title")
        if payload.action == "close_window" and payload.hwnd is None and not payload.title:
            raise ValueError("close_window requires hwnd or title")
        if payload.action == "kill_process" and payload.pid is None and not payload.process_name and not payload.title:
            raise ValueError("kill_process requires pid, process_name or title")
        if payload.action == "clipboard_set" and payload.text is None:
            raise ValueError("clipboard_set requires text")
        if payload.action == "notify" and payload.message is None:
            raise ValueError("notify requires message")
        if payload.action == "service_action":
            if not payload.service_name or not payload.service_action:
                raise ValueError("service_action requires service_name and service_action")

    async def _run(self, payload: DesktopRequest) -> ActionResult:
        target = {
            key: value
            for key, value in {
                "hwnd": payload.hwnd,
                "title": payload.title,
                "path": payload.path,
                "dest": payload.dest,
                "new_name": payload.new_name,
                "app_name": payload.app_name,
                "app_path": payload.app_path,
                "process_name": payload.process_name,
                "pid": payload.pid,
                "service_name": payload.service_name,
                "service_action": payload.service_action,
            }.items()
            if value is not None
        }
        semantic_type = "mutation" if payload.action in {"close_window", "kill_process", "clipboard_set", "notify", "service_action", "explorer_rename_path", "explorer_copy_path", "explorer_move_path"} else "inspection"
        if payload.action in {"open_app", "open_path", "open_file", "focus_window", "explorer_select_path", "explorer_navigate"}:
            semantic_type = "execution"
        changed = payload.action in {"close_window", "kill_process", "clipboard_set", "notify", "service_action", "explorer_rename_path", "explorer_copy_path", "explorer_move_path"}
        try:
            if payload.action == "list_processes":
                details = await self.agent.list_processes()
                summary = "Listei os processos em execucao."
            elif payload.action == "list_windows":
                details = await self.agent.list_windows()
                summary = "Listei as janelas abertas."
            elif payload.action == "list_explorer_windows":
                details = await self.agent.list_explorer_windows()
                summary = "Listei as janelas do Explorer."
            elif payload.action == "list_mapped_drives":
                details = await self.agent.list_mapped_drives()
                summary = "Listei os drives mapeados."
            elif payload.action == "get_explorer_selection":
                details = await self.agent.get_explorer_selection()
                summary = "Capturei a selecao atual do Explorer."
            elif payload.action == "explorer_context":
                details = await self.agent.explorer_context()
                summary = "Capturei o contexto aprofundado do Explorer."
            elif payload.action == "explorer_select_path":
                details = await self.agent.explorer_select_path(payload.path or "")
                summary = f"Abri o Explorer selecionando {payload.path}."
            elif payload.action == "explorer_navigate":
                details = await self.agent.explorer_navigate(payload.path or "")
                summary = f"Naveguei o Explorer para {payload.path}."
            elif payload.action == "explorer_rename_path":
                details = await self.agent.explorer_rename_path(payload.path or "", payload.new_name or "")
                summary = f"Renomeei {payload.path}."
            elif payload.action == "explorer_copy_path":
                details = await self.agent.explorer_copy_path(payload.path or "", payload.dest or "")
                summary = f"Copiei {payload.path}."
            elif payload.action == "explorer_move_path":
                details = await self.agent.explorer_move_path(payload.path or "", payload.dest or "")
                summary = f"Movi {payload.path}."
            elif payload.action == "inspect_installer":
                details = await self.agent.inspect_installer(payload.path or "")
                summary = f"Inspecionei o instalador {payload.path}."
            elif payload.action == "open_app":
                details = await self.agent.open_app(
                    app_name=payload.app_name,
                    app_path=payload.app_path,
                    arguments=payload.arguments,
                )
                summary = f"Abri o app {payload.app_name or payload.app_path}."
            elif payload.action == "open_path":
                details = await self.agent.open_path(payload.path or "")
                summary = f"Abri o caminho {payload.path}."
            elif payload.action == "open_file":
                details = await self.agent.open_file(payload.path or "")
                summary = f"Abri o arquivo {payload.path}."
            elif payload.action == "wait_for_window":
                details = await self.agent.wait_for_window(
                    hwnd=payload.hwnd,
                    title=payload.title,
                    process_name=payload.process_name,
                    timeout_seconds=payload.timeout_seconds or 15,
                )
                summary = "Detectei a janela solicitada."
            elif payload.action == "focus_window":
                details = await self.agent.focus_window(hwnd=payload.hwnd, title=payload.title)
                summary = "Coloquei a janela em foco."
            elif payload.action == "close_window":
                details = await self.agent.close_window(hwnd=payload.hwnd, title=payload.title)
                summary = "Solicitei o fechamento da janela."
            elif payload.action == "kill_process":
                details = await self.agent.kill_process(pid=payload.pid, process_name=payload.process_name, title=payload.title)
                summary = "Solicitei a finalizacao do processo."
            elif payload.action == "clipboard_get":
                details = await self.agent.clipboard_get()
                summary = "Li o texto atual da area de transferencia."
            elif payload.action == "clipboard_set":
                details = await self.agent.clipboard_set(payload.text or "")
                summary = "Atualizei a area de transferencia."
            elif payload.action == "notify":
                details = await self.agent.notify(payload.message or "", title=payload.title or "Agente Desktop")
                summary = "Enviei a notificacao."
            elif payload.action == "list_services":
                details = await self.agent.list_services()
                summary = "Listei os servicos do Windows."
            elif payload.action == "service_action":
                details = await self.agent.service_action(payload.service_name or "", payload.service_action or "")
                summary = f"Executei a acao {payload.service_action} no servico {payload.service_name}."
            else:
                return ActionResult(
                    status="failed",
                    summary=f"A acao {payload.action} nao e suportada.",
                    tool="desktop",
                    action=payload.action,
                    semantic_type=semantic_type,
                    target=target,
                    data={},
                    effects=ActionEffects(changed=False),
                    issue=ActionIssue(kind="unsupported_action", message=f"Unsupported desktop action: {payload.action}"),
                )
            return ActionResult(
                status="succeeded",
                summary=summary,
                tool="desktop",
                action=payload.action,
                semantic_type=semantic_type,
                target=target,
                data=details,
                effects=ActionEffects(changed=changed),
            )
        except FileNotFoundError as exc:
            issue_kind = "app_not_found" if payload.action == "open_app" else "path_not_found"
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="desktop",
                action=payload.action,
                semantic_type=semantic_type,
                target=target,
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind=issue_kind, message=str(exc), retryable=False),
                diagnostics={"exception_type": exc.__class__.__name__},
            )
        except TimeoutError as exc:
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="desktop",
                action=payload.action,
                semantic_type=semantic_type,
                target=target,
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="timeout", message=str(exc), retryable=True),
                diagnostics={"exception_type": exc.__class__.__name__},
            )
        except ValueError as exc:
            message = str(exc)
            lowered = message.lower()
            if "window not found" in lowered:
                issue_kind = "window_not_found"
            elif "process" in lowered and "not found" in lowered:
                issue_kind = "process_not_found"
            else:
                issue_kind = "validation"
            return ActionResult(
                status="failed",
                summary=message,
                tool="desktop",
                action=payload.action,
                semantic_type=semantic_type,
                target=target,
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind=issue_kind, message=message, retryable=False),
                diagnostics={"exception_type": exc.__class__.__name__},
            )
        except Exception as exc:
            logger.exception("Desktop action failed")
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="desktop",
                action=payload.action,
                semantic_type=semantic_type,
                target=target,
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="tool_internal", message=str(exc), retryable=False),
                diagnostics={"exception_type": exc.__class__.__name__},
            )
