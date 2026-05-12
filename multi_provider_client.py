from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

from provider_registry import get_provider


class NormalizedToolCall(BaseModel):
    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class AgentTurnResult(BaseModel):
    content: str = ""
    tool_calls: List[NormalizedToolCall] = Field(default_factory=list)


class MultiProviderClient:
    def __init__(self, *, timeout: float = 45.0):
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
        if provider_id in {"openai", "openai_compatible"}:
            return await self._complete_openai_compatible(
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
        raise ValueError(f"unsupported provider: {provider}")

    async def test_connection(
        self,
        *,
        provider: str,
        api_key: str,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        provider_id = get_provider(provider).provider
        if provider_id in {"openai", "openai_compatible"}:
            url = f"{(base_url or get_provider(provider_id).base_url).rstrip('/')}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
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
        }
        headers = {"Authorization": f"Bearer {api_key}"}
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
        return AgentTurnResult(content=self._coerce_content(message.get("content")), tool_calls=tool_calls)

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
        payload: Dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "tools": self._to_anthropic_tools(tools),
            "max_tokens": 1024,
        }
        if system:
            payload["system"] = system
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{base_url.rstrip('/')}/messages", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        text_chunks: List[str] = []
        tool_calls: List[NormalizedToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_chunks.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    NormalizedToolCall(
                        id=block["id"],
                        name=block["name"],
                        arguments=block.get("input", {}),
                    )
                )
        return AgentTurnResult(content="\n".join(chunk for chunk in text_chunks if chunk).strip(), tool_calls=tool_calls)

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
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message["tool_call_id"],
                                "content": self._coerce_content(message.get("content")),
                            }
                        ],
                    }
                )
                continue
            blocks: List[Dict[str, Any]] = []
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
