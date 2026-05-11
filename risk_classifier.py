import re
from typing import List, Dict

RISK_RULES = [
    (r"\bformat\b|\bshutdown\b|\bdel\b.*[A-Za-z]:\\", 'high', ['destructive']),
    (r"\breg\s+(add|delete|deletevalue)\b|\bsc\s+delete\b|\bbcdedit\b|\btakeown\b|\bicacls\b", 'high', ['system','registry']),
    (r"\bmsiexec\b|\binstall\b|\bchoco\b|\bwget\b|\bcurl\b|\bInvoke-WebRequest\b", 'medium', ['network','installer']),
    (r"\bnet\s+user\b|\bnet\s+localgroup\b", 'medium', ['accounts']),
    (r"\bpowershell\b|\bcmd\b|\bpipe\b|\|\b", 'low', ['shell']),
]


def classify(command: str) -> Dict:
    tags = []
    level = 'low'
    for pattern, p_level, p_tags in RISK_RULES:
        if re.search(pattern, command, re.IGNORECASE):
            tags.extend(p_tags)
            # escalate level
            if p_level == 'high':
                level = 'high'
                break
            if p_level == 'medium' and level != 'high':
                level = 'medium'
    return {'level': level, 'tags': list(set(tags)), 'reason': 'matched rules' if tags else 'no specific match'}
