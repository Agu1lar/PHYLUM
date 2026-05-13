# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

from urllib.parse import urlparse


def domain_trust(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    if hostname.endswith((".microsoft.com", ".windows.com", ".hp.com", ".canon.com", ".epson.com", ".brother.com", ".openai.com", ".anthropic.com", ".python.org")):
        return "official"
    if hostname:
        return "unknown"
    return "invalid"


def describe_url(url: str) -> dict:
    hostname = (urlparse(url).hostname or "").lower()
    return {
        "url": url,
        "hostname": hostname,
        "trust": domain_trust(url),
    }
