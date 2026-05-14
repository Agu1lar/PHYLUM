# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool facade for the persistent codebase map."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from action_models import ActionResult
from codebase_map import CodebaseMap
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class CodebaseMapRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(scan|stats|find_symbol|find_imports|find_tests|find_routes|"
                "find_configs|find_owners|get_file|dependency_graph|search)$",
    )
    workspace: Optional[str] = None
    name: Optional[str] = None
    kind: Optional[str] = None
    module: Optional[str] = None
    path: Optional[str] = None
    path_pattern: Optional[str] = None
    config_type: Optional[str] = None
    owner: Optional[str] = None
    query: Optional[str] = None
    force: bool = False


class CodebaseMapTool(BaseTool):
    InputModel = CodebaseMapRequest

    def __init__(self):
        super().__init__(default_timeout=120, default_retries=1)
        self._maps: Dict[str, CodebaseMap] = {}

    def _get_map(self, workspace: Optional[str] = None) -> CodebaseMap:
        ws = workspace or os.getcwd()
        if ws not in self._maps:
            self._maps[ws] = CodebaseMap(ws)
        return self._maps[ws]

    async def _run(self, payload: CodebaseMapRequest) -> ActionResult:
        action = payload.action
        cmap = self._get_map(payload.workspace)

        try:
            if action == "scan":
                result = cmap.scan(force=payload.force)
                return ActionResult(
                    status="succeeded",
                    summary=f"Scanned workspace: {result['new']} new, {result['updated']} updated, "
                            f"{result['removed']} removed, {result['unchanged']} unchanged files "
                            f"in {result['elapsed_seconds']}s",
                    tool="codebase_map", action=action,
                    data=result,
                )

            if action == "stats":
                result = cmap.stats()
                return ActionResult(
                    status="succeeded",
                    summary=f"Codebase: {result['files']} files, {result['symbols']} symbols, "
                            f"{result['tests']} tests, {result['routes']} routes",
                    tool="codebase_map", action=action,
                    data=result,
                )

            if action == "find_symbol":
                if not payload.name:
                    return ActionResult(
                        status="failed", summary="'name' is required for find_symbol",
                        tool="codebase_map", action=action,
                    )
                results = cmap.find_symbol(payload.name, payload.kind)
                return ActionResult(
                    status="succeeded",
                    summary=f"Found {len(results)} symbol(s) matching '{payload.name}'",
                    tool="codebase_map", action=action,
                    data={"matches": results, "count": len(results)},
                )

            if action == "find_imports":
                if not payload.module:
                    return ActionResult(
                        status="failed", summary="'module' is required for find_imports",
                        tool="codebase_map", action=action,
                    )
                results = cmap.find_imports(payload.module)
                return ActionResult(
                    status="succeeded",
                    summary=f"Found {len(results)} file(s) importing '{payload.module}'",
                    tool="codebase_map", action=action,
                    data={"matches": results, "count": len(results)},
                )

            if action == "find_tests":
                if not payload.module:
                    return ActionResult(
                        status="failed", summary="'module' is required for find_tests",
                        tool="codebase_map", action=action,
                    )
                results = cmap.find_tests_for(payload.module)
                return ActionResult(
                    status="succeeded",
                    summary=f"Found {len(results)} test(s) for module '{payload.module}'",
                    tool="codebase_map", action=action,
                    data={"matches": results, "count": len(results)},
                )

            if action == "find_routes":
                results = cmap.find_routes(payload.path_pattern)
                return ActionResult(
                    status="succeeded",
                    summary=f"Found {len(results)} route(s)",
                    tool="codebase_map", action=action,
                    data={"matches": results, "count": len(results)},
                )

            if action == "find_configs":
                results = cmap.find_configs(payload.config_type)
                return ActionResult(
                    status="succeeded",
                    summary=f"Found {len(results)} config(s)",
                    tool="codebase_map", action=action,
                    data={"matches": results, "count": len(results)},
                )

            if action == "find_owners":
                results = cmap.find_owners(payload.owner)
                return ActionResult(
                    status="succeeded",
                    summary=f"Found {len(results)} ownership record(s)",
                    tool="codebase_map", action=action,
                    data={"matches": results, "count": len(results)},
                )

            if action == "get_file":
                if not payload.path:
                    return ActionResult(
                        status="failed", summary="'path' is required for get_file",
                        tool="codebase_map", action=action,
                    )
                result = cmap.get_file(payload.path)
                if result is None:
                    return ActionResult(
                        status="failed",
                        summary=f"File '{payload.path}' not found in codebase map",
                        tool="codebase_map", action=action,
                    )
                return ActionResult(
                    status="succeeded",
                    summary=f"File info for '{payload.path}'",
                    tool="codebase_map", action=action,
                    data=result,
                )

            if action == "dependency_graph":
                if not payload.path:
                    return ActionResult(
                        status="failed", summary="'path' is required for dependency_graph",
                        tool="codebase_map", action=action,
                    )
                result = cmap.dependency_graph(payload.path)
                return ActionResult(
                    status="succeeded",
                    summary=f"Dependency graph for '{payload.path}': "
                            f"{len(result.get('imports', []))} imports, "
                            f"{len(result.get('imported_by', []))} importers",
                    tool="codebase_map", action=action,
                    data=result,
                )

            if action == "search":
                if not payload.query:
                    return ActionResult(
                        status="failed", summary="'query' is required for search",
                        tool="codebase_map", action=action,
                    )
                results = cmap.search(payload.query)
                total = sum(len(v) for v in results.values() if isinstance(v, list))
                return ActionResult(
                    status="succeeded",
                    summary=f"Search '{payload.query}': {total} result(s) across all dimensions",
                    tool="codebase_map", action=action,
                    data=results,
                )

            return ActionResult(
                status="failed", summary=f"Unknown action: {action}",
                tool="codebase_map", action=action,
            )

        except Exception as exc:
            return ActionResult(
                status="failed", summary=str(exc),
                tool="codebase_map", action=action,
            )
