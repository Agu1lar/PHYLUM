# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Offline handlers for golden tasks that do not map 1:1 to a single tool call."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Union

ROOT = Path(__file__).resolve().parent.parent
for sub in ("core", "tools", "agents", "nodes", "models", "providers", "safety", "memory", "execution", "persistence"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

HandlerFn = Callable[["RunContext", Dict[str, Any]], Union[Dict[str, Any], Awaitable[Dict[str, Any]]]]


@dataclass
class RunContext:
    work_dir: Path
    fixtures_root: Path
    platform: str = "windows"
    has_playwright: bool = False
    skip_requires: bool = False


def _classify_dialog(ctx: RunContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from windows_ui_agent import WindowsUiAgent

    window = {"title": args.get("title", ""), "control_type": args.get("control_type", "Window")}
    children = list(args.get("controls") or [])
    return WindowsUiAgent()._classify_dialog(window, children)


async def _planner_route(ctx: RunContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from planner_agent import PlannerAgent

    plan, validation = await PlannerAgent().parse(args["prompt"])
    first = plan.tasks[0] if plan.tasks else None
    return {
        "ok": validation.ok,
        "tool": getattr(first, "tool", None),
        "action": getattr(first, "action", None),
        "errors": list(validation.errors or []),
    }


def _extract_links_fixture(ctx: RunContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from tool_web import _LinkParser

    fixture = args.get("fixture", "web/search_results.html")
    html = (ctx.fixtures_root / fixture).read_text(encoding="utf-8")
    parser = _LinkParser()
    parser.feed(html)
    return {"links": parser.links, "count": len(parser.links)}


async def _office_validate(ctx: RunContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from tool_office import OfficeTool

    tool = OfficeTool()
    payload = dict(args.get("payload") or {})
    try:
        await tool.validate(tool.InputModel(**payload))
        return {"validation_error": None}
    except ValueError as exc:
        return {"validation_error": str(exc)}


async def _browser_validate(ctx: RunContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from tool_browser import BrowserTool

    tool = BrowserTool()
    payload = dict(args.get("payload") or {})
    try:
        await tool.validate(tool.InputModel(**payload))
        return {"validation_error": None}
    except ValueError as exc:
        return {"validation_error": str(exc)}


async def _web_validate(ctx: RunContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from tool_web import WebTool

    tool = WebTool()
    payload = dict(args.get("payload") or {})
    try:
        await tool.validate(tool.InputModel(**payload))
        return {"validation_error": None}
    except ValueError as exc:
        return {"validation_error": str(exc)}


HANDLERS: Dict[str, HandlerFn] = {
    "windows_ui.classify_dialog": _classify_dialog,
    "planner.route": _planner_route,
    "web.extract_links_fixture": _extract_links_fixture,
    "web.validate": _web_validate,
    "office.validate": _office_validate,
    "browser.validate": _browser_validate,
}
