"""Dynamic Tool Creator: runtime creation and persistence of micro-tools.

Allows the agent to write small, purpose-built tools during a run to handle
scenarios not covered by native tools. These micro-tools are Python functions
that get persisted to disk and can be reused across runs.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
import types
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DYNAMIC_TOOLS_DIR = Path(os.getenv("AGENTE_DYNAMIC_TOOLS_DIR", "")) or (Path.home() / ".agente" / "dynamic_tools")
MAX_CODE_LENGTH = 32_000
MANIFEST_FILE = "manifest.json"


def _ensure_tools_dir() -> Path:
    DYNAMIC_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    return DYNAMIC_TOOLS_DIR


class DynamicToolSpec:
    __slots__ = ("tool_id", "name", "description", "code", "language", "created_at", "tags", "version")

    def __init__(
        self,
        *,
        tool_id: str,
        name: str,
        description: str,
        code: str,
        language: str = "python",
        created_at: Optional[str] = None,
        tags: Optional[List[str]] = None,
        version: int = 1,
    ):
        self.tool_id = tool_id
        self.name = name
        self.description = description
        self.code = code
        self.language = language
        self.created_at = created_at or datetime.utcnow().isoformat()
        self.tags = tags or []
        self.version = version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "description": self.description,
            "code": self.code,
            "language": self.language,
            "created_at": self.created_at,
            "tags": self.tags,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DynamicToolSpec":
        return cls(
            tool_id=data["tool_id"],
            name=data["name"],
            description=data.get("description", ""),
            code=data["code"],
            language=data.get("language", "python"),
            created_at=data.get("created_at"),
            tags=data.get("tags", []),
            version=data.get("version", 1),
        )


class DynamicToolResult:
    __slots__ = ("ok", "output", "error", "tool_id", "tool_name")

    def __init__(self, *, ok: bool, output: Any = None, error: Optional[str] = None, tool_id: str = "", tool_name: str = ""):
        self.ok = ok
        self.output = output
        self.error = error
        self.tool_id = tool_id
        self.tool_name = tool_name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
        }


class DynamicToolCreator:
    def __init__(self, *, tools_dir: Optional[Path] = None):
        self.tools_dir = tools_dir or DYNAMIC_TOOLS_DIR
        self._loaded: Dict[str, DynamicToolSpec] = {}
        self._modules: Dict[str, types.ModuleType] = {}

    def _ensure_dir(self) -> Path:
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        return self.tools_dir

    def _manifest_path(self) -> Path:
        return self.tools_dir / MANIFEST_FILE

    def _load_manifest(self) -> Dict[str, Any]:
        manifest_path = self._manifest_path()
        if manifest_path.exists():
            try:
                return json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                return {"tools": {}}
        return {"tools": {}}

    def _save_manifest(self, manifest: Dict[str, Any]) -> None:
        self._ensure_dir()
        self._manifest_path().write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    async def create_tool(
        self,
        *,
        name: str,
        description: str,
        code: str,
        language: str = "python",
        tags: Optional[List[str]] = None,
    ) -> DynamicToolSpec:
        if len(code) > MAX_CODE_LENGTH:
            raise ValueError(f"Code exceeds maximum length of {MAX_CODE_LENGTH} characters")
        if language not in {"python", "powershell"}:
            raise ValueError(f"Unsupported language: {language}")

        self._ensure_dir()
        tool_id = f"dyn_{uuid.uuid4().hex[:10]}"
        spec = DynamicToolSpec(
            tool_id=tool_id,
            name=name,
            description=description,
            code=code,
            language=language,
            tags=tags,
        )

        ext = ".py" if language == "python" else ".ps1"
        code_path = self.tools_dir / f"{tool_id}{ext}"
        code_path.write_text(code, encoding="utf-8")

        manifest = self._load_manifest()
        manifest["tools"][tool_id] = spec.to_dict()
        self._save_manifest(manifest)

        self._loaded[tool_id] = spec
        logger.info("Created dynamic tool %s (%s)", name, tool_id)
        return spec

    async def list_tools(self, *, tag: Optional[str] = None) -> List[DynamicToolSpec]:
        manifest = self._load_manifest()
        tools: List[DynamicToolSpec] = []
        for data in manifest.get("tools", {}).values():
            spec = DynamicToolSpec.from_dict(data)
            if tag and tag not in spec.tags:
                continue
            tools.append(spec)
        return tools

    async def get_tool(self, tool_id: str) -> Optional[DynamicToolSpec]:
        if tool_id in self._loaded:
            return self._loaded[tool_id]
        manifest = self._load_manifest()
        data = manifest.get("tools", {}).get(tool_id)
        if data:
            spec = DynamicToolSpec.from_dict(data)
            self._loaded[tool_id] = spec
            return spec
        return None

    async def delete_tool(self, tool_id: str) -> bool:
        manifest = self._load_manifest()
        if tool_id not in manifest.get("tools", {}):
            return False
        spec_data = manifest["tools"].pop(tool_id)
        self._save_manifest(manifest)

        lang = spec_data.get("language", "python")
        ext = ".py" if lang == "python" else ".ps1"
        code_path = self.tools_dir / f"{tool_id}{ext}"
        if code_path.exists():
            code_path.unlink()

        self._loaded.pop(tool_id, None)
        self._modules.pop(tool_id, None)
        return True

    async def execute_tool(
        self,
        tool_id: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 60,
    ) -> DynamicToolResult:
        spec = await self.get_tool(tool_id)
        if spec is None:
            return DynamicToolResult(ok=False, error=f"Tool {tool_id} not found", tool_id=tool_id)

        if spec.language == "python":
            return await self._execute_python_tool(spec, params or {}, timeout)
        if spec.language == "powershell":
            return await self._execute_powershell_tool(spec, params or {}, timeout)

        return DynamicToolResult(ok=False, error=f"Unsupported language: {spec.language}", tool_id=tool_id, tool_name=spec.name)

    async def _execute_python_tool(self, spec: DynamicToolSpec, params: Dict[str, Any], timeout: int) -> DynamicToolResult:
        try:
            module = self._load_python_module(spec)
            run_fn = getattr(module, "run", None)
            if run_fn is None:
                return DynamicToolResult(ok=False, error="Dynamic tool must define a 'run' function", tool_id=spec.tool_id, tool_name=spec.name)

            if asyncio.iscoroutinefunction(run_fn):
                result = await asyncio.wait_for(run_fn(params), timeout=timeout)
            else:
                result = await asyncio.wait_for(asyncio.to_thread(run_fn, params), timeout=timeout)

            return DynamicToolResult(ok=True, output=result, tool_id=spec.tool_id, tool_name=spec.name)
        except asyncio.TimeoutError:
            return DynamicToolResult(ok=False, error="Execution timed out", tool_id=spec.tool_id, tool_name=spec.name)
        except Exception as exc:
            return DynamicToolResult(ok=False, error=str(exc), tool_id=spec.tool_id, tool_name=spec.name)

    async def _execute_powershell_tool(self, spec: DynamicToolSpec, params: Dict[str, Any], timeout: int) -> DynamicToolResult:
        from sandbox_executor import SandboxExecutor

        executor = SandboxExecutor(default_timeout=timeout)
        params_json = json.dumps(params, default=str)
        wrapped_code = f'$params = \'{params_json}\' | ConvertFrom-Json\n{spec.code}'
        result = await executor.execute_powershell(wrapped_code, timeout=timeout)
        return DynamicToolResult(
            ok=result.ok,
            output=result.stdout if result.ok else None,
            error=result.error or result.stderr if not result.ok else None,
            tool_id=spec.tool_id,
            tool_name=spec.name,
        )

    def _load_python_module(self, spec: DynamicToolSpec) -> types.ModuleType:
        if spec.tool_id in self._modules:
            return self._modules[spec.tool_id]

        code_path = self.tools_dir / f"{spec.tool_id}.py"
        if not code_path.exists():
            code_path.write_text(spec.code, encoding="utf-8")

        module_name = f"agente_dynamic_{spec.tool_id}"
        loader = importlib.util.spec_from_file_location(module_name, str(code_path))
        if loader is None or loader.loader is None:
            raise RuntimeError(f"Cannot load module from {code_path}")
        module = importlib.util.module_from_spec(loader)
        sys.modules[module_name] = module
        loader.loader.exec_module(module)
        self._modules[spec.tool_id] = module
        return module
