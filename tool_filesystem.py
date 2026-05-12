import asyncio
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from pathlib import Path
import shutil
from tool_base import BaseTool
from tool_schemas import ToolResult
from agent_persistence import Persistence
from fs_agent import FileSystemAgent

logger = logging.getLogger(__name__)

import tempfile

from action_models import ActionEffects, ActionIssue, ActionResult

HOME = Path.home()
# include common temp dir to support pytest tmp_path and system temp locations
ALLOWED_ROOTS = [HOME, Path('C:/Users/Public'), Path('C:/Temp'), Path(tempfile.gettempdir())]
NETWORK_READONLY_ACTIONS = {'read', 'list', 'stat', 'find_files'}


def _is_allowed_path(p: Path) -> bool:
    try:
        p = p.resolve()
        for root in ALLOWED_ROOTS:
            try:
                if root.resolve() in p.parents or root.resolve() == p:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def _is_network_path(path_value: str) -> bool:
    normalized = (path_value or "").strip().replace("/", "\\")
    return normalized.startswith("\\\\")


def is_allowed_path_for_action(path_value: str, action: str) -> bool:
    if _is_network_path(path_value):
        return action in NETWORK_READONLY_ACTIONS
    return _is_allowed_path(Path(path_value))


class FSInput(BaseModel):
    action: str = Field(..., pattern='^(read|write|delete|move|mkdir|organize_directory|organize_downloads|organize_desktop|detect_duplicates|clean_temp|create_structure|undo|find_files|list|stat|copy)$')
    path: Optional[str] = None
    content: Optional[str] = None
    dest: Optional[str] = None
    pattern: Optional[str] = None
    template: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None
    backup: bool = Field(True)
    allow_outside_sandbox: bool = Field(False)


class FileSystemTool(BaseTool):
    InputModel = FSInput
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 2):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.persistence = Persistence.get()
        self.agent = FileSystemAgent()

    async def validate(self, payload: FSInput) -> None:
        if payload.path:
            if not payload.allow_outside_sandbox and not is_allowed_path_for_action(payload.path, payload.action):
                raise ValueError('path not allowed by sandbox')
        if payload.action in ('write',) and payload.content is None:
            raise ValueError('content required for write')
        if payload.action == 'move' and payload.dest is None:
            raise ValueError('dest required for move')
        if payload.action == 'copy' and payload.dest is None:
            raise ValueError('dest required for copy')
        if payload.action in {'read', 'write', 'delete', 'move', 'mkdir', 'organize_directory', 'detect_duplicates', 'clean_temp', 'create_structure', 'find_files', 'list', 'stat', 'copy'} and not payload.path:
            raise ValueError('path is required')
        if payload.action == 'find_files' and not payload.pattern:
            raise ValueError('pattern is required')
        if payload.action == 'create_structure' and not payload.template:
            raise ValueError('template is required')
        if payload.action == 'undo' and not payload.request_id:
            raise ValueError('request_id is required')
        if payload.dest:
            dst = Path(payload.dest)
            if not payload.allow_outside_sandbox and (_is_network_path(payload.dest) or not _is_allowed_path(dst)):
                raise ValueError('dest not allowed')

    async def _read(self, path: Path) -> str:
        return await asyncio.to_thread(path.read_text, encoding='utf-8', errors='ignore')

    async def _write(self, path: Path, content: str) -> None:
        await asyncio.to_thread(path.write_text, content, encoding='utf-8')

    async def _delete(self, path: Path) -> None:
        await asyncio.to_thread(path.unlink)

    async def _mkdir(self, path: Path) -> None:
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)

    async def _move(self, src: Path, dst: Path) -> None:
        # Ensure destination parent exists to avoid FileNotFoundError
        await asyncio.to_thread(dst.parent.mkdir, parents=True, exist_ok=True)
        if not src.exists():
            raise FileNotFoundError(f"source not found: {src}")
        await asyncio.to_thread(src.replace, dst)

    async def _copy(self, src: Path, dst: Path) -> None:
        await asyncio.to_thread(dst.parent.mkdir, parents=True, exist_ok=True)
        if not src.exists():
            raise FileNotFoundError(f"source not found: {src}")
        await asyncio.to_thread(shutil.copy2, src, dst)

    async def _run(self, payload: FSInput) -> ActionResult:
        p = Path(payload.path) if payload.path else None
        backup_key = f"fs:backup:{str(p)}" if p else None
        target = {key: value for key, value in {"path": payload.path, "dest": payload.dest, "pattern": payload.pattern, "request_id": payload.request_id}.items() if value is not None}
        try:
            if payload.action == 'read':
                data = await self._read(p)
                return ActionResult(status="succeeded", summary=f"Li o conteudo de {payload.path}.", tool="filesystem", action=payload.action, semantic_type="inspection", target=target, data={"content": data}, effects=ActionEffects(changed=False))
            if payload.action == 'write':
                if payload.backup and p.exists():
                    old = await self._read(p)
                    await self.persistence.save_kv(backup_key, {'content': old})
                await self._write(p, payload.content or '')
                return ActionResult(status="succeeded", summary=f"Gravei o arquivo {payload.path}.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data={}, effects=ActionEffects(changed=True, rollback={"available": bool(backup_key), "reference": backup_key}))
            if payload.action == 'delete':
                if payload.backup and p.exists():
                    old = await self._read(p)
                    await self.persistence.save_kv(backup_key, {'content': old})
                await self._delete(p)
                return ActionResult(status="succeeded", summary=f"Exclui {payload.path}.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data={}, effects=ActionEffects(changed=True, rollback={"available": bool(backup_key), "reference": backup_key}))
            if payload.action == 'mkdir':
                await self._mkdir(p)
                return ActionResult(status="succeeded", summary=f"Criei o diretorio {payload.path}.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data={}, effects=ActionEffects(changed=True))
            if payload.action == 'move':
                dst = Path(payload.dest)
                if not payload.allow_outside_sandbox and not _is_allowed_path(dst):
                    raise ValueError('dest not allowed')
                if payload.backup and p.exists():
                    old = await self._read(p)
                    await self.persistence.save_kv(backup_key, {'content': old})
                await self._move(p, dst)
                return ActionResult(status="succeeded", summary=f"Movei {payload.path} para {payload.dest}.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data={"dest": str(dst)}, effects=ActionEffects(changed=True, rollback={"available": bool(backup_key), "reference": backup_key}))
            if payload.action == 'copy':
                dst = Path(payload.dest)
                await self._copy(p, dst)
                return ActionResult(status="succeeded", summary=f"Copiei {payload.path} para {payload.dest}.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data={"dest": str(dst)}, effects=ActionEffects(changed=True))
            if payload.action == 'list':
                items = []
                for child in sorted(p.iterdir(), key=lambda item: item.name.lower()):
                    items.append({"name": child.name, "path": str(child), "is_dir": child.is_dir(), "size": child.stat().st_size if child.is_file() else None})
                return ActionResult(status="succeeded", summary=f"Listei {len(items)} item(ns) em {payload.path}.", tool="filesystem", action=payload.action, semantic_type="inspection", target=target, data={"items": items}, effects=ActionEffects(changed=False))
            if payload.action == 'stat':
                info = p.stat()
                return ActionResult(
                    status="succeeded",
                    summary=f"Consultei os metadados de {payload.path}.",
                    tool="filesystem",
                    action=payload.action,
                    semantic_type="inspection",
                    target=target,
                    data={
                        'path': str(p),
                        'is_dir': p.is_dir(),
                        'exists': p.exists(),
                        'size': info.st_size,
                        'modified_at': info.st_mtime,
                    },
                    effects=ActionEffects(changed=False),
                )
            if payload.action == 'find_files':
                matches = await self.agent.find_files(p, payload.pattern or '*')
                return ActionResult(status="succeeded", summary=f"Encontrei {len(matches)} arquivo(s) em {payload.path}.", tool="filesystem", action=payload.action, semantic_type="inspection", target=target, data={'matches': matches}, effects=ActionEffects(changed=False))
            if payload.action == 'organize_directory':
                result = await self.agent.organize_directory(p)
                return ActionResult(status="succeeded", summary=f"Organizei o diretorio {payload.path}.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data=result, effects=ActionEffects(changed=True))
            if payload.action == 'organize_downloads':
                result = await self.agent.organize_downloads()
                return ActionResult(status="succeeded", summary="Organizei a pasta de Downloads.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data=result, effects=ActionEffects(changed=True))
            if payload.action == 'organize_desktop':
                result = await self.agent.organize_desktop()
                return ActionResult(status="succeeded", summary="Organizei a area de trabalho.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data=result, effects=ActionEffects(changed=True))
            if payload.action == 'detect_duplicates':
                result = await self.agent.detect_duplicates(p)
                return ActionResult(status="succeeded", summary=f"Analisei duplicidades em {payload.path}.", tool="filesystem", action=payload.action, semantic_type="inspection", target=target, data=result, effects=ActionEffects(changed=False))
            if payload.action == 'clean_temp':
                result = await self.agent.clean_temp([p])
                return ActionResult(status="succeeded", summary=f"Limpei arquivos temporarios em {payload.path}.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data=result, effects=ActionEffects(changed=True))
            if payload.action == 'create_structure':
                result = await self.agent.create_structure(p, payload.template or {})
                return ActionResult(status="succeeded", summary=f"Criei a estrutura em {payload.path}.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data=result, effects=ActionEffects(changed=True))
            if payload.action == 'undo':
                result = await self.agent.undo(payload.request_id or '')
                return ActionResult(status="succeeded", summary=f"Tentei desfazer a operacao {payload.request_id}.", tool="filesystem", action=payload.action, semantic_type="mutation", target=target, data={'result': result}, effects=ActionEffects(changed=True))
            return ActionResult(
                status="failed",
                summary=f"A acao {payload.action} nao e suportada.",
                tool="filesystem",
                action=payload.action,
                semantic_type="mutation",
                target=target,
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="unsupported_action", message=f"Unsupported filesystem action: {payload.action}"),
            )
        except Exception as exc:
            logger.exception('Filesystem op failed')
            # attempt rollback if backup exists
            try:
                b = await self.persistence.get_kv(backup_key) if backup_key else None
                if b and 'content' in b:
                    await self._write(p, b['content'])
                    logger.info('Rollback applied from backup for %s', p)
            except Exception:
                logger.exception('Rollback failed')
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="filesystem",
                action=payload.action,
                semantic_type="mutation" if payload.action not in {"read", "list", "stat", "find_files", "detect_duplicates"} else "inspection",
                target=target,
                data={},
                effects=ActionEffects(changed=False, rollback={"available": bool(backup_key), "reference": backup_key}),
                issue=ActionIssue(kind="tool_failed", message=str(exc), details={"backup_reference": backup_key}),
            )
