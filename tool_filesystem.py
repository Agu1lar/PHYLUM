import asyncio
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from pathlib import Path
from tool_base import BaseTool
from tool_schemas import ToolResult
from agent_persistence import Persistence

logger = logging.getLogger(__name__)

import tempfile

HOME = Path.home()
# include common temp dir to support pytest tmp_path and system temp locations
ALLOWED_ROOTS = [HOME, Path('C:/Users/Public'), Path('C:/Temp'), Path(tempfile.gettempdir())]


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


class FSInput(BaseModel):
    action: str = Field(..., pattern='^(read|write|delete|move|mkdir)$')
    path: str
    content: Optional[str] = None
    dest: Optional[str] = None
    backup: bool = Field(True)


class FSOutput(BaseModel):
    success: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class FileSystemTool(BaseTool):
    InputModel = FSInput
    OutputModel = FSOutput

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 2):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.persistence = Persistence.get()

    async def validate(self, payload: FSInput) -> None:
        p = Path(payload.path)
        if not _is_allowed_path(p):
            raise ValueError('path not allowed by sandbox')
        if payload.action in ('write',) and payload.content is None:
            raise ValueError('content required for write')
        if payload.action == 'move' and payload.dest is None:
            raise ValueError('dest required for move')

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

    async def _run(self, payload: FSInput) -> FSOutput:
        p = Path(payload.path)
        backup_key = f"fs:backup:{str(p)}"
        try:
            if payload.action == 'read':
                data = await self._read(p)
                return FSOutput(success=True, message='read', details={'content': data})
            if payload.action == 'write':
                if payload.backup and p.exists():
                    old = await self._read(p)
                    await self.persistence.save_kv(backup_key, {'content': old})
                await self._write(p, payload.content or '')
                return FSOutput(success=True, message='written')
            if payload.action == 'delete':
                if payload.backup and p.exists():
                    old = await self._read(p)
                    await self.persistence.save_kv(backup_key, {'content': old})
                await self._delete(p)
                return FSOutput(success=True, message='deleted')
            if payload.action == 'mkdir':
                await self._mkdir(p)
                return FSOutput(success=True, message='mkdir')
            if payload.action == 'move':
                dst = Path(payload.dest)
                if not _is_allowed_path(dst):
                    raise ValueError('dest not allowed')
                if payload.backup and p.exists():
                    old = await self._read(p)
                    await self.persistence.save_kv(backup_key, {'content': old})
                await self._move(p, dst)
                return FSOutput(success=True, message='moved', details={'dest': str(dst)})
            return FSOutput(success=False, message='unknown action')
        except Exception as exc:
            logger.exception('Filesystem op failed')
            # attempt rollback if backup exists
            try:
                b = await self.persistence.get_kv(backup_key)
                if b and 'content' in b:
                    await self._write(p, b['content'])
                    logger.info('Rollback applied from backup for %s', p)
            except Exception:
                logger.exception('Rollback failed')
            raise
