import hashlib
from pathlib import Path
import logging
import shutil
import asyncio
from typing import Optional
from fs_config import PROTECTED_PATHS, QUARANTINE_DIR, AGENT_WORKSPACE
from agent_persistence import Persistence

logger = logging.getLogger(__name__)


def is_protected(path: Path) -> bool:
    try:
        p = path.resolve()
        for prot in PROTECTED_PATHS:
            try:
                if prot.resolve() in p.parents or prot.resolve() == p:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return True


def ensure_quarantine(path: Path) -> Path:
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    return QUARANTINE_DIR


async def compute_hash(path: Path, chunk_size: int = 8192) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    try:
        # run file reading in thread to avoid blocking
        def _read():
            with path.open('rb') as f:
                for chunk in iter(lambda: f.read(chunk_size), b''):
                    h.update(chunk)
        await asyncio.to_thread(_read)
        return h.hexdigest()
    except Exception:
        logger.exception('hash failed for %s', path)
        return None


async def safe_move(src: Path, dst: Path) -> None:
    if is_protected(src) or is_protected(dst):
        raise PermissionError('protected path')
    dst.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.move, str(src), str(dst))


async def safe_delete(path: Path) -> Path:
    """Move file to quarantine and return quarantine path."""
    if is_protected(path):
        raise PermissionError('protected path')
    q = ensure_quarantine(path)
    # create unique destination
    import time
    ts = int(time.time() * 1000)
    dest = q / f"{path.name}.{ts}"
    await asyncio.to_thread(shutil.move, str(path), str(dest))
    return dest


async def safe_write(path: Path, content: bytes) -> None:
    if is_protected(path):
        raise PermissionError('protected path')
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, content)


async def list_files(root: Path, *, recursive=True):
    files = []
    if not root.exists():
        return files
    if recursive:
        for p in root.rglob('*'):
            if p.is_file():
                files.append(p)
    else:
        for p in root.iterdir():
            if p.is_file():
                files.append(p)
    return files


async def ensure_workspace():
    AGENT_WORKSPACE.mkdir(parents=True, exist_ok=True)
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    return AGENT_WORKSPACE
