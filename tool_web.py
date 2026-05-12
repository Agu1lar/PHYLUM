import hashlib
import logging
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urljoin

import httpx
from pydantic import BaseModel, Field

from download_policy import describe_url
from tool_base import BaseTool

logger = logging.getLogger(__name__)


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
                response = await client.get(url, headers={"User-Agent": "AgenteDesktop/1.0"})
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
                return WebOutput(success=True, message="search_web", details={"query": payload.query, "candidates": candidates})

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
                ranked = sorted(
                    candidates,
                    key=lambda item: (0 if item.get("trust") == "official" else 1, item.get("url") or ""),
                )
                return WebOutput(success=True, message="summarize_candidates", details={"ranked": ranked[:10]})

        raise ValueError(f"unsupported web action: {payload.action}")
