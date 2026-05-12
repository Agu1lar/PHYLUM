import re
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

DEFAULT_BLACKLIST: List[str] = []

class CommandValidator:
    def __init__(self, blacklist: Optional[List[str]] = None, whitelist: Optional[List[str]] = None, allow_protected_paths: bool = False):
        self.blacklist_patterns = []
        for p in (blacklist or DEFAULT_BLACKLIST):
            try:
                self.blacklist_patterns.append(re.compile(p, re.IGNORECASE))
            except re.error as e:
                logger.warning("Skipping invalid blacklist regex '%s': %s", p, e)
        self.whitelist = []
        for w in (whitelist or []):
            try:
                self.whitelist.append(re.compile(w, re.IGNORECASE))
            except re.error as e:
                logger.warning("Skipping invalid whitelist regex '%s': %s", w, e)
        self.allow_protected_paths = allow_protected_paths

    def is_whitelisted(self, command: str) -> bool:
        if not self.whitelist:
            return False
        for p in self.whitelist:
            if p.search(command):
                return True
        return False

    def is_blacklisted(self, command: str) -> Optional[str]:
        for p in self.blacklist_patterns:
            if p.search(command):
                return p.pattern
        return None

    def validate(self, command: str) -> (bool, Optional[str]):
        """Return (allowed, reason).

        The approval layer is responsible for deciding whether a risky command
        should run. Validation here only rejects malformed input that cannot be
        executed safely by the runtime itself.
        """
        cmd = command.strip()
        if not cmd:
            return False, "empty command"
        if self.is_whitelisted(cmd):
            return True, None
        if len(cmd) > 2048:
            return False, "command too long"
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", cmd):
            return False, "contains control characters"
        return True, None
