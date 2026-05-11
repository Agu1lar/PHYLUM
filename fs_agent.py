import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from fs_utils import list_files, safe_move, safe_delete, compute_hash, ensure_workspace
from fs_classifier import FileClassifier
from duplicate_detector import find_duplicates
from rollback_manager import RollbackManager, OperationRecord
from monitor import DirectoryMonitor
from fs_config import DESKTOP_DIR, DOWNLOADS_DIR
from pydantic import BaseModel
import uuid
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class AgentConfig(BaseModel):
    allowed_roots: List[str] = []


class FileSystemAgent:
    def __init__(self):
        self.classifier = FileClassifier()
        self.rollback = RollbackManager()
        self.monitor = DirectoryMonitor()

    async def startup(self):
        await ensure_workspace()

    async def organize_directory(self, root: Path, dry_run: bool = False) -> Dict[str, Any]:
        """Organize files under root by category into subfolders."""
        if not root.exists():
            raise FileNotFoundError(root)
        files = await list_files(root, recursive=False)
        moved = []
        request_id = str(uuid.uuid4())
        for f in files:
            try:
                cat = self.classifier.classify(f)
                target_dir = root / cat
                target_dir.mkdir(parents=True, exist_ok=True)
                dst = target_dir / f.name
                if dry_run:
                    logger.info('[dry] would move %s -> %s', f, dst)
                    continue
                await safe_move(f, dst)
                moved.append((str(f), str(dst)))
                # record for rollback
                op = OperationRecord(id=str(uuid.uuid4()), type='move', src=str(f), dst=str(dst))
                await self.rollback.record(request_id, op)
            except Exception:
                logger.exception('organize item failed %s', f)
        return {'request_id': request_id, 'moved': moved}

    async def organize_desktop(self, dry_run: bool = False) -> Dict[str, Any]:
        return await self.organize_directory(DESKTOP_DIR, dry_run=dry_run)

    async def organize_downloads(self, dry_run: bool = False) -> Dict[str, Any]:
        return await self.organize_directory(DOWNLOADS_DIR, dry_run=dry_run)

    async def move_by_category(self, src_root: Path, category: str, dest_root: Path, dry_run: bool = False) -> Dict[str, Any]:
        files = await list_files(src_root, recursive=True)
        moved = []
        request_id = str(uuid.uuid4())
        for f in files:
            try:
                if self.classifier.classify(f) == category:
                    rel = f.relative_to(src_root)
                    dst = dest_root / rel
                    if dry_run:
                        logger.info('[dry] would move %s -> %s', f, dst)
                        continue
                    await safe_move(f, dst)
                    moved.append((str(f), str(dst)))
                    op = OperationRecord(id=str(uuid.uuid4()), type='move', src=str(f), dst=str(dst))
                    await self.rollback.record(request_id, op)
            except Exception:
                logger.exception('move_by_category failed for %s', f)
        return {'request_id': request_id, 'moved': moved}

    async def detect_duplicates(self, root: Path) -> Dict[str, Any]:
        dups = await find_duplicates(root)
        logger.info('Detected %s duplicate groups', len(dups))
        return {'duplicates': dups}

    async def clean_temp(self, roots: Optional[List[Path]] = None, dry_run: bool = False) -> Dict[str, Any]:
        roots = roots or []
        removed = []
        request_id = str(uuid.uuid4())
        for r in roots:
            files = await list_files(r, recursive=True)
            for f in files:
                if f.suffix.lower() in ('.tmp', '.log') or f.name.endswith('~'):
                    try:
                        if dry_run:
                            logger.info('[dry] would delete %s', f)
                            continue
                        dest = await safe_delete(f)
                        removed.append((str(f), str(dest)))
                        op = OperationRecord(id=str(uuid.uuid4()), type='delete', src=str(f), dst=str(dest))
                        await self.rollback.record(request_id, op)
                    except Exception:
                        logger.exception('clean_temp failed for %s', f)
        return {'request_id': request_id, 'removed': removed}

    async def find_files(self, root: Path, pattern: str) -> List[str]:
        res = []
        for p in root.rglob(pattern):
            if p.is_file():
                res.append(str(p))
        return res

    async def create_structure(self, root: Path, template: Dict[str, Any]) -> Dict[str, Any]:
        """Template is dict of folder_name: subtemplate or None"""
        created = []
        request_id = str(uuid.uuid4())
        async def _create(base: Path, tpl: Dict[str, Any]):
            for name, sub in tpl.items():
                p = base / name
                p.mkdir(parents=True, exist_ok=True)
                created.append(str(p))
                if isinstance(sub, dict):
                    await _create(p, sub)
        await _create(root, template)
        return {'request_id': request_id, 'created': created}

    async def monitor_directories(self, paths: List[str], callback):
        # callback receives watchdog event - run in background
        self.monitor.start(paths, callback)
        return {'monitoring': paths}

    async def undo(self, request_id: str, n: int = 1):
        return await self.rollback.undo_last(request_id, n)


# Example usage helper
async def example_usage():
    agent = FileSystemAgent()
    await agent.startup()
    res = await agent.organize_desktop(dry_run=True)
    print(res)

if __name__ == '__main__':
    asyncio.run(example_usage())
