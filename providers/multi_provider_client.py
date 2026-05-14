# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

from provider_registry import get_provider

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 529}
_MAX_LLM_RETRIES = 3
_BACKOFF_BASE = 2.0


class LLMApiError(Exception):
    """Raised when the LLM API returns an unrecoverable error after retries."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        model: str = "",
        status_code: int = 0,
        response_body: str = "",
    ):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.response_body = response_body


class NormalizedToolCall(BaseModel):
    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class AgentTurnResult(BaseModel):
    content: str = ""
    tool_calls: List[NormalizedToolCall] = Field(default_factory=list)
    thinking: str = ""
    thinking_blocks: List[Dict[str, Any]] = Field(default_factory=list)
    usage: Dict[str, int] = Field(default_factory=dict)


class MultiProviderClient:
    def __init__(self, *, timeout: float = 30.0):
        self.timeout = timeout

    async def complete(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        base_url: Optional[str] = None,
    ) -> AgentTurnResult:
        provider_id = get_provider(provider).provider
        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_LLM_RETRIES + 1):
            try:
                return await self._dispatch(
                    provider_id=provider_id,
                    api_key=api_key,
                    model=model,
                    messages=messages,
                    tools=tools,
                    base_url=base_url,
                )
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                if status not in _RETRYABLE_STATUS_CODES or attempt == _MAX_LLM_RETRIES:
                    logger.error(
                        "LLM API %s/%s returned HTTP %d (attempt %d/%d, non-retryable or exhausted)",
                        provider_id, model, status, attempt, _MAX_LLM_RETRIES,
                    )
                    raise LLMApiError(
                        f"LLM API error: HTTP {status} from {provider_id}/{model}",
                        provider=provider_id,
                        model=model,
                        status_code=status,
                        response_body=exc.response.text[:500] if exc.response else "",
                    ) from exc
                retry_after = self._parse_retry_after(exc.response)
                wait = retry_after if retry_after else min(_BACKOFF_BASE ** attempt, 30.0)
                logger.warning(
                    "LLM API %s/%s returned HTTP %d — retrying in %.1fs (attempt %d/%d)",
                    provider_id, model, status, wait, attempt, _MAX_LLM_RETRIES,
                )
                await asyncio.sleep(wait)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt == _MAX_LLM_RETRIES:
                    raise LLMApiError(
                        f"LLM API connection failed after {_MAX_LLM_RETRIES} attempts: {exc}",
                        provider=provider_id,
                        model=model,
                        status_code=0,
                    ) from exc
                wait = min(_BACKOFF_BASE ** attempt, 15.0)
                logger.warning(
                    "LLM API %s/%s connection issue: %s — retrying in %.1fs (attempt %d/%d)",
                    provider_id, model, exc, wait, attempt, _MAX_LLM_RETRIES,
                )
                await asyncio.sleep(wait)
        raise last_exc or RuntimeError("LLM API call failed unexpectedly")

    async def _dispatch(
        self,
        *,
        provider_id: str,
        api_key: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        base_url: Optional[str] = None,
    ) -> AgentTurnResult:
        if provider_id in {"openai", "openai_compatible", "openrouter"}:
            return await self._complete_openai_compatible(
                provider_id=provider_id,
                api_key=api_key,
                model=model,
                messages=messages,
                tools=tools,
                base_url=base_url or get_provider(provider_id).base_url,
            )
        if provider_id == "gemini":
            return await self._complete_gemini(
                api_key=api_key,
                model=model,
                messages=messages,
                tools=tools,
                base_url=base_url or get_provider(provider_id).base_url,
            )
        if provider_id == "anthropic":
            return await self._complete_anthropic(
                api_key=api_key,
                model=model,
                messages=messages,
                tools=tools,
                base_url=base_url or get_provider(provider_id).base_url,
            )
        raise ValueError(f"unsupported provider: {provider_id}")

    @staticmethod
    def _parse_retry_after(response: Optional[httpx.Response]) -> Optional[float]:
        if response is None:
            return None
        header = response.headers.get("retry-after")
        if not header:
            return None
        try:
            return float(header)
        except (ValueError, TypeError):
            return None

    async def test_connection(
        self,
        *,
        provider: str,
        api_key: str,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        provider_id = get_provider(provider).provider
        if provider_id in {"openai", "openai_compatible", "openrouter"}:
            url = f"{(base_url or get_provider(provider_id).base_url).rstrip('/')}/models"
            headers = self._openai_compatible_headers(api_key=api_key, provider_id=provider_id)
        elif provider_id == "gemini":
            url = f"{(base_url or get_provider(provider_id).base_url).rstrip('/')}/models"
            headers = {"x-goog-api-key": api_key}
        elif provider_id == "anthropic":
            url = f"{(base_url or get_provider(provider_id).base_url).rstrip('/')}/models"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
        else:
            raise ValueError(f"unsupported provider: {provider}")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return {"ok": True, "status_code": response.status_code}

    async def _complete_openai_compatible(
        self,
        *,
        provider_id: str,
        api_key: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        base_url: str,
    ) -> AgentTurnResult:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 2048,
        }
        headers = self._openai_compatible_headers(api_key=api_key, provider_id=provider_id)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        message = data["choices"][0]["message"]
        tool_calls = [
            NormalizedToolCall(
                id=tool_call["id"],
                name=tool_call["function"]["name"],
                arguments=json.loads(tool_call["function"]["arguments"] or "{}"),
            )
            for tool_call in message.get("tool_calls", [])
        ]
        usage = self._extract_usage(data)
        return AgentTurnResult(content=self._coerce_content(message.get("content")), tool_calls=tool_calls, usage=usage)

    async def _complete_gemini(
        self,
        *,
        api_key: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        base_url: str,
    ) -> AgentTurnResult:
        payload = self._to_gemini_payload(messages=messages, tools=tools)
        headers = {"x-goog-api-key": api_key}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/models/{model}:generateContent",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        candidate = (data.get("candidates") or [{}])[0]
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        text_chunks: List[str] = []
        tool_calls: List[NormalizedToolCall] = []
        for index, part in enumerate(parts, start=1):
            if "text" in part:
                text_chunks.append(part.get("text", ""))
            elif "functionCall" in part:
                function_call = part["functionCall"]
                tool_calls.append(
                    NormalizedToolCall(
                        id=function_call.get("id") or f"gemini-tool-{index}",
                        name=function_call["name"],
                        arguments=function_call.get("args") or {},
                    )
                )
        usage = self._extract_usage(data)
        return AgentTurnResult(content="\n".join(chunk for chunk in text_chunks if chunk).strip(), tool_calls=tool_calls, usage=usage)

    ANTHROPIC_THINKING_MODELS = {"claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-7"}

    async def _complete_anthropic(
        self,
        *,
        api_key: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        base_url: str,
    ) -> AgentTurnResult:
        system, anthropic_messages = self._to_anthropic_messages(messages)
        anthropic_tools = self._to_anthropic_tools(tools)
        use_cache = any(
            isinstance(t, dict) and "cache_control" in t for t in tools
        )
        if use_cache:
            for i, tool in enumerate(anthropic_tools):
                src = tools[i] if i < len(tools) else {}
                if isinstance(src, dict) and "cache_control" in src:
                    tool["cache_control"] = src["cache_control"]

        use_thinking = self._model_supports_thinking(model)

        if anthropic_tools:
            anthropic_tools[-1]["cache_control"] = {"type": "ephemeral"}

        payload: Dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "tools": anthropic_tools,
            "max_tokens": 8000 if use_thinking else 2048,
        }
        if use_thinking:
            payload["thinking"] = {"type": "adaptive"}
        if system:
            payload["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
        }

        timeout = max(self.timeout, 60.0) if use_thinking else self.timeout
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base_url.rstrip('/')}/messages", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        text_chunks: List[str] = []
        tool_calls: List[NormalizedToolCall] = []
        thinking_text_chunks: List[str] = []
        thinking_blocks: List[Dict[str, Any]] = []

        for block in data.get("content", []):
            block_type = block.get("type")
            if block_type == "thinking":
                text = block.get("thinking", "")
                if text:
                    thinking_text_chunks.append(text)
                thinking_blocks.append(block)
            elif block_type == "redacted_thinking":
                thinking_blocks.append(block)
            elif block_type == "text":
                text_chunks.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    NormalizedToolCall(
                        id=block["id"],
                        name=block["name"],
                        arguments=block.get("input", {}),
                    )
                )

        usage = self._extract_usage(data)
        return AgentTurnResult(
            content="\n".join(chunk for chunk in text_chunks if chunk).strip(),
            tool_calls=tool_calls,
            thinking="\n".join(thinking_text_chunks).strip(),
            thinking_blocks=thinking_blocks,
            usage=usage,
        )

    @staticmethod
    def _extract_usage(data: Dict[str, Any]) -> Dict[str, int]:
        """Normalize token usage from any provider response."""
        usage = data.get("usage") or data.get("usageMetadata") or {}
        return {
            "prompt_tokens": usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("total_tokens", 0) or usage.get("totalTokenCount", 0),
            "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        }

    def _model_supports_thinking(self, model: str) -> bool:
        base = model.split("-2")[0] if "-2" in model else model
        return any(base.startswith(prefix) for prefix in ("claude-sonnet-4", "claude-opus-4"))

    def _to_anthropic_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        anthropic_tools: List[Dict[str, Any]] = []
        for tool in tools:
            function_def = tool["function"]
            anthropic_tools.append(
                {
                    "name": function_def["name"],
                    "description": function_def.get("description", ""),
                    "input_schema": function_def["parameters"],
                }
            )
        return anthropic_tools

    def _to_anthropic_messages(self, messages: List[Dict[str, Any]]) -> Any:
        system_parts: List[str] = []
        converted: List[Dict[str, Any]] = []
        for message in messages:
            role = message["role"]
            if role == "system":
                if message.get("content"):
                    system_parts.append(self._coerce_content(message.get("content")))
                continue
            if role == "tool":
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": self._coerce_content(message.get("content")),
                }
                if converted and converted[-1]["role"] == "user" and self._is_tool_result_message(converted[-1]):
                    converted[-1]["content"].append(tool_result_block)
                else:
                    converted.append(
                        {"role": "user", "content": [tool_result_block]}
                    )
                continue
            blocks: List[Dict[str, Any]] = []
            for tb in message.get("_thinking_blocks") or []:
                blocks.append(tb)
            content = self._coerce_content(message.get("content"))
            if content:
                blocks.append({"type": "text", "text": content})
            for tool_call in message.get("tool_calls", []):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_call["id"],
                        "name": tool_call["function"]["name"],
                        "input": json.loads(tool_call["function"]["arguments"] or "{}"),
                    }
                )
            converted.append({"role": role, "content": blocks or [{"type": "text", "text": ""}]})
        return "\n".join(system_parts).strip(), converted

    @staticmethod
    def _is_tool_result_message(message: Dict[str, Any]) -> bool:
        content = message.get("content")
        if not isinstance(content, list):
            return False
        return all(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in content
        )

    def _to_gemini_payload(self, *, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        system_instruction, contents = self._to_gemini_messages(messages)
        payload: Dict[str, Any] = {
            "contents": contents,
            "tools": [{"functionDeclarations": self._to_gemini_tools(tools)}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        return payload

    def _to_gemini_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        function_definitions: List[Dict[str, Any]] = []
        for tool in tools:
            function_def = tool["function"]
            function_definitions.append(
                {
                    "name": function_def["name"],
                    "description": function_def.get("description", ""),
                    "parameters": function_def["parameters"],
                }
            )
        return function_definitions

    def _to_gemini_messages(self, messages: List[Dict[str, Any]]) -> Any:
        system_parts: List[str] = []
        contents: List[Dict[str, Any]] = []
        tool_name_by_id: Dict[str, str] = {}

        for message in messages:
            role = message["role"]
            if role == "system":
                if message.get("content"):
                    system_parts.append(self._coerce_content(message.get("content")))
                continue

            if role == "tool":
                tool_call_id = message["tool_call_id"]
                function_name = tool_name_by_id.get(tool_call_id, "tool_result")
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": function_name,
                                    "response": self._coerce_tool_response_content(message.get("content")),
                                }
                            }
                        ],
                    }
                )
                continue

            parts: List[Dict[str, Any]] = []
            content = self._coerce_content(message.get("content"))
            if content:
                parts.append({"text": content})
            for tool_call in message.get("tool_calls", []):
                tool_name_by_id[tool_call["id"]] = tool_call["function"]["name"]
                parts.append(
                    {
                        "functionCall": {
                            "name": tool_call["function"]["name"],
                            "args": json.loads(tool_call["function"]["arguments"] or "{}"),
                        }
                    }
                )
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": parts or [{"text": ""}],
                }
            )

        return "\n".join(system_parts).strip(), contents

    def _coerce_tool_response_content(self, value: Any) -> Dict[str, Any]:
        if value is None:
            return {"content": ""}
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"content": self._coerce_content(value)}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
                return {"content": parsed}
            except json.JSONDecodeError:
                return {"content": value}
        return {"content": value}

    def _openai_compatible_headers(self, *, api_key: str, provider_id: str) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {api_key}"}
        if provider_id == "openrouter":
            headers["HTTP-Referer"] = "http://127.0.0.1:5173"
            headers["X-Title"] = "PHYLUM"
        return headers

    def _coerce_content(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            chunks: List[str] = []
            for item in value:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    chunks.append(item.get("text") or item.get("content") or "")
            return "\n".join(chunk for chunk in chunks if chunk).strip()
        return str(value)
