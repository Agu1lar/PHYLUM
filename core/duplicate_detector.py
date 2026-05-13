from pathlib import Path
from typing import Dict, List
import asyncio
from fs_utils import compute_hash, list_files
import logging

logger = logging.getLogger(__name__)


async def find_duplicates(root: Path) -> Dict[str, List[str]]:
    """Return dict: hash -> [filepaths] where length>1 indicates duplicates."""
    files = await list_files(root, recursive=True)
    hashes = {}

    async def _hash_file(p):
        h = await compute_hash(p)
        return p, h

    tasks = [asyncio.create_task(_hash_file(p)) for p in files]
    for t in asyncio.as_completed(tasks):
        p, h = await t
        if h is None:
            continue
        hashes.setdefault(h, []).append(str(p))
    # filter
    return {h: fps for h, fps in hashes.items() if len(fps) > 1}
