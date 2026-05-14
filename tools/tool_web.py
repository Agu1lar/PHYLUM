# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
import hashlib
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from pydantic import BaseModel, Field

from download_policy import describe_url
from agent_persistence import Persistence
from tool_base import BaseTool
from world_model import WorldModel

logger = logging.getLogger(__name__)

WEB_SEARCH_CACHE_TTL_SECONDS = 86400 * 3
PREFERRED_WEB_HOSTS = (
    "learn.microsoft.com",
    "docs.microsoft.com",
    "stackoverflow.com",
    "serverfault.com",
    "superuser.com",
)
PREFERRED_HOST_SUFFIXES = (
    ".microsoft.com",
    ".windows.com",
)


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: List[Dict[str, str]] = []
        self._current_href: Optional[str] = None
        self._current_text: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() == "a":
            self._current_href = dict(attrs).get("href")
            self._current_text = []

    def handle_data(self, data: str):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._current_href:
            text = " ".join(piece.strip() for piece in self._current_text if piece.strip()).strip()
            self.links.append({"href": self._current_href, "text": text})
            self._current_href = None
            self._current_text = []


class WebInput(BaseModel):
    action: str = Field(..., pattern='^(search_web|fetch_readonly|extract_links|check_url|download_verified|summarize_candidates)$')
    query: Optional[str] = None
    url: Optional[str] = None
    download_dir: Optional[str] = None
    checksum: Optional[str] = None
    algorithm: Optional[str] = "sha256"
    candidates: Optional[List[Dict[str, Any]]] = None


class WebOutput(BaseModel):
    success: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class WebTool(BaseTool):
    InputModel = WebInput
    OutputModel = WebOutput

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 2, world_model: Optional[WorldModel] = None):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.world_model = world_model or WorldModel(Persistence.get())

    async def validate(self, payload: WebInput) -> None:
        if payload.action == "search_web" and not payload.query:
            raise ValueError("query is required")
        if payload.action in {"fetch_readonly", "extract_links", "check_url", "download_verified"} and not payload.url:
            raise ValueError("url is required")
        if payload.action == "download_verified" and not payload.download_dir:
            raise ValueError("download_dir is required")
        if payload.action == "summarize_candidates" and not payload.candidates:
            raise ValueError("candidates are required")

    async def _run(self, payload: WebInput) -> WebOutput:
        if payload.action == "search_web":
            cached = await self._get_cached_search(payload.query or "")
            if cached is not None:
                return WebOutput(success=True, message="search_web", details=cached)

        async with httpx.AsyncClient(timeout=self.default_timeout, follow_redirects=True) as client:
            if payload.action == "check_url":
                response = await client.get(payload.url)
                return WebOutput(
                    success=response.status_code < 400,
                    message="check_url",
                    details={
                        "status_code": response.status_code,
                        "final_url": str(response.url),
                        **describe_url(str(response.url)),
                    },
                )

            if payload.action == "fetch_readonly":
                response = await client.get(payload.url)
                text = response.text[:5000]
                return WebOutput(
                    success=response.status_code < 400,
                    message="fetch_readonly",
                    details={
                        "status_code": response.status_code,
                        "final_url": str(response.url),
                        "content": text,
                        **describe_url(str(response.url)),
                    },
                )

            if payload.action == "extract_links":
                response = await client.get(payload.url)
                parser = _LinkParser()
                parser.feed(response.text)
                links = []
                for link in parser.links[:50]:
                    href = link.get("href")
                    if href:
                        links.append(
                            {
                                "url": urljoin(str(response.url), href),
                                "text": link.get("text") or "",
                            }
                        )
                return WebOutput(success=True, message="extract_links", details={"links": links})

            if payload.action == "search_web":
                url = f"https://html.duckduckgo.com/html/?q={quote_plus(payload.query or '')}"
                response = await client.get(url, headers={"User-Agent": "PHYLUM/1.0"})
                parser = _LinkParser()
                parser.feed(response.text)
                candidates = []
                for link in parser.links:
                    href = link.get("href")
                    if not href or href.startswith("/"):
                        continue
                    candidate = {"url": href, "text": link.get("text") or ""}
                    candidate.update(describe_url(href))
                    candidates.append(candidate)
                    if len(candidates) >= 10:
                        break
                candidates = self._rank_candidates(candidates)
                details = {"query": payload.query, "candidates": candidates, "cache_hit": False}
                await self._cache_search(payload.query or "", details)
                return WebOutput(success=True, message="search_web", details=details)

            if payload.action == "download_verified":
                response = await client.get(payload.url)
                download_dir = Path(payload.download_dir or ".")
                download_dir.mkdir(parents=True, exist_ok=True)
                filename = Path(str(response.url).split("?")[0]).name or "download.bin"
                destination = download_dir / filename
                destination.write_bytes(response.content)

                digest = None
                algorithm = (payload.algorithm or "sha256").lower()
                if algorithm:
                    hasher = hashlib.new(algorithm)
                    hasher.update(response.content)
                    digest = hasher.hexdigest()
                checksum_ok = True
                if payload.checksum:
                    checksum_ok = digest == payload.checksum.lower()
                return WebOutput(
                    success=response.status_code < 400 and checksum_ok,
                    message="download_verified",
                    details={
                        "path": str(destination),
                        "checksum": digest,
                        "checksum_ok": checksum_ok,
                        "status_code": response.status_code,
                        **describe_url(str(response.url)),
                    },
                )

            if payload.action == "summarize_candidates":
                candidates = payload.candidates or []
                ranked = self._rank_candidates(candidates)
                return WebOutput(success=True, message="summarize_candidates", details={"ranked": ranked[:10]})

        raise ValueError(f"unsupported web action: {payload.action}")

    async def _get_cached_search(self, query: str) -> Optional[Dict[str, Any]]:
        key = self._search_cache_key(query)
        if not key:
            return None
        entity = await self.world_model.get("web_resource", key)
        if entity is None or not isinstance(entity.value, dict):
            return None
        details = dict(entity.value)
        details["cache_hit"] = True
        details.setdefault("query", query)
        details["candidates"] = self._rank_candidates(details.get("candidates") or [])
        return details

    async def _cache_search(self, query: str, details: Dict[str, Any]) -> None:
        key = self._search_cache_key(query)
        if not key:
            return
        try:
            await self.world_model.upsert(
                "web_resource",
                key,
                {
                    "query": query,
                    "candidates": details.get("candidates") or [],
                    "provider": "duckduckgo_html",
                    "purpose": "autonomous_discovery_web_search",
                },
                confidence=0.75,
                source="web.search_web",
                tags=["web_search", "discovery", "cache"],
                ttl_seconds=WEB_SEARCH_CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.debug("Failed to cache web search for query=%r", query, exc_info=True)

    @staticmethod
    def _search_cache_key(query: str) -> str:
        normalized = re.sub(r"\s+", " ", (query or "").strip().lower())
        if not normalized:
            return ""
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        return f"search:{digest}"

    @staticmethod
    def _quality_score(candidate: Dict[str, Any]) -> int:
        url = candidate.get("url") or ""
        hostname = (candidate.get("hostname") or urlparse(url).hostname or "").lower()
        trust = candidate.get("trust") or describe_url(url).get("trust")
        if hostname in PREFERRED_WEB_HOSTS:
            return 0
        if any(hostname.endswith(suffix) for suffix in PREFERRED_HOST_SUFFIXES):
            return 1
        if trust == "official":
            return 2
        if "stackoverflow.com" in hostname:
            return 0
        return 3

    @classmethod
    def _rank_candidates(cls, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enriched = []
        for index, item in enumerate(candidates):
            candidate = dict(item)
            url = candidate.get("url") or ""
            if url and ("hostname" not in candidate or "trust" not in candidate):
                candidate.update(describe_url(url))
            candidate["quality_rank"] = cls._quality_score(candidate)
            candidate["_original_index"] = index
            enriched.append(candidate)
        ranked = sorted(enriched, key=lambda item: (item["quality_rank"], item.get("hostname") or "", item["_original_index"]))
        for item in ranked:
            item.pop("_original_index", None)
        return ranked
