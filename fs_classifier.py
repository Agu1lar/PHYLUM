from pathlib import Path
from typing import Dict, Optional
from fs_config import DEFAULT_CATEGORIES


class FileClassifier:
    def __init__(self, categories: Optional[Dict[str, list]] = None):
        self.categories = categories or DEFAULT_CATEGORIES

    def classify(self, path: Path) -> str:
        ext = path.suffix.lower()
        for cat, exts in self.categories.items():
            if ext in exts:
                return cat
        # fallback based on mime-like heuristics
        if ext in ('.tmp', '.log'):
            return 'temporary'
        return 'others'

    def target_folder_for(self, root: Path, category: str) -> Path:
        return root / category
