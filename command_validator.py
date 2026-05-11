import re
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

# Conservative defaults
DEFAULT_BLACKLIST = [
    r"\bformat\b",
    r"\bshutdown\b",
    r"\bdel\b.*[A-Za-z]:\\",
    r"\brm\b",
    r"\brmdir\b",
    r"\brd\b.*\/s",
    r"\bmove\b.*[A-Za-z]:\\",
    r"\bcopy\b.*[A-Za-z]:\\Windows",
    r"\breg\s+(add|delete|deletevalue)\b",
    r"\bsc\s+delete\b",
    r"\bbcdedit\b",
    r"\btakeown\b",
    r"\bicacls\b",
    r"\bnet\s+user\b",
    r"\bnet\s+localgroup\b",
    r"\bcertutil\b.*-urlcache",
    r"\bmsiexec\b.*\/i",
    r"\bpowershell\b.*-EncodedCommand",
]

class CommandValidator:
    def __init__(self, blacklist: Optional[List[str]] = None, whitelist: Optional[List[str]] = None, allow_protected_paths: bool = False):
        # Safely compile patterns, skip invalid regexes but log them
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
        """Return (allowed, reason)."""
        cmd = command.strip()
        if not cmd:
            return False, "empty command"
        if self.is_whitelisted(cmd):
            return True, None
        black = self.is_blacklisted(cmd)
        if black:
            return False, f"blacklist match: {black}"
        # logical sandbox checks: protected paths
        if not self.allow_protected_paths:
            # simple heuristic: touching C:\Windows or C:\Program Files
            protected_paths = ["C:\\Windows", "C:\\Program Files"]
            for pp in protected_paths:
                # escape path for regex and allow optional surrounding quotes
                pat = rf'["\']?{re.escape(pp)}["\']?'
                if re.search(pat, cmd, re.IGNORECASE):
                    return False, "targets protected system paths"
        # length and complexity limits
        if len(cmd) > 2048:
            return False, "command too long"
        # disallow control characters
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", cmd):
            return False, "contains control characters"
        return True, None
