from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


SUPPORTED_TOOL_NAMES = [
    "shell",
    "filesystem",
    "memory",
    "browser",
    "web",
    "package_manager",
    "software_inventory",
    "env_manager",
    "driver_manager",
    "os",
    "desktop",
]


def supported_tools() -> List[str]:
    return list(SUPPORTED_TOOL_NAMES)


def tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "Execute a Windows command through the protected shell runtime.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to execute."},
                        "shell": {"type": "string", "enum": ["powershell", "cmd"]},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 180},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "filesystem",
                "description": "Read or modify files inside allowed roots.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "read",
                                "write",
                                "delete",
                                "move",
                                "mkdir",
                                "organize_directory",
                                "organize_downloads",
                                "organize_desktop",
                                "detect_duplicates",
                                "clean_temp",
                                "create_structure",
                                "undo",
                                "find_files",
                                "list",
                                "stat",
                                "copy",
                            ],
                        },
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "dest": {"type": "string"},
                        "pattern": {"type": "string"},
                        "template": {"type": "object"},
                        "request_id": {"type": "string"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory",
                "description": "Store or retrieve structured memory entries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["set", "get", "delete"]},
                        "key": {"type": "string"},
                        "value": {"type": "object"},
                    },
                    "required": ["action", "key"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser",
                "description": "Use Playwright-based browser automation without pixel control.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["open_page", "search", "download", "scrape_structured", "interact_dom", "upload_file"],
                        },
                        "url": {"type": "string"},
                        "base_url": {"type": "string"},
                        "query_selector": {"type": "string"},
                        "query": {"type": "string"},
                        "result_selector": {"type": "string"},
                        "extractors": {"type": "object"},
                        "actions": {"type": "array"},
                        "selector": {"type": "string"},
                        "file_path": {"type": "string"},
                        "headless": {"type": "boolean"},
                        "browser": {"type": "string", "enum": ["chromium", "firefox", "webkit"]},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 180},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web",
                "description": "Use safe web research and validated downloads.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["search_web", "fetch_readonly", "extract_links", "check_url", "download_verified", "summarize_candidates"],
                        },
                        "query": {"type": "string"},
                        "url": {"type": "string"},
                        "download_dir": {"type": "string"},
                        "checksum": {"type": "string"},
                        "algorithm": {"type": "string"},
                        "candidates": {"type": "array"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "package_manager",
                "description": "Manage packages through supported package managers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["install", "uninstall", "list", "search", "show", "upgrade"]},
                        "manager": {"type": "string", "enum": ["choco", "pip", "winget"]},
                        "package": {"type": "string"},
                        "version": {"type": "string"},
                        "source": {"type": "string"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "software_inventory",
                "description": "Inspect installed software and resolve executables on Windows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list_installed", "search_installed", "find_executable", "resolve_command", "find_install_location", "find_uninstaller"],
                        },
                        "query": {"type": "string"},
                        "command": {"type": "string"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "env_manager",
                "description": "Read and edit user or process environment variables safely.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["get", "set", "unset", "append_path", "remove_path", "list_path_entries", "backup", "restore"],
                        },
                        "name": {"type": "string"},
                        "value": {"type": "string"},
                        "scope": {"type": "string", "enum": ["process", "user"]},
                        "entry": {"type": "string"},
                        "backup_id": {"type": "string"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "driver_manager",
                "description": "Inspect devices and manage Windows driver-related actions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "list_devices",
                                "device_status",
                                "list_drivers",
                                "find_driver_candidates",
                                "install_inf",
                                "add_driver_package",
                                "rollback_driver",
                                "scan_hardware_changes",
                                "printer_status",
                                "printer_driver_info",
                                "restart_spooler",
                            ],
                        },
                        "query": {"type": "string"},
                        "device_id": {"type": "string"},
                        "path": {"type": "string"},
                        "printer_name": {"type": "string"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "os",
                "description": "Inspect the Windows operating system using native APIs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["overview", "apps", "processes", "full"]},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "desktop",
                "description": "Use Windows-native desktop primitives instead of shell automation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "list_processes",
                                "list_windows",
                                "focus_window",
                                "clipboard_get",
                                "clipboard_set",
                                "notify",
                                "list_services",
                                "service_action",
                            ],
                        },
                        "hwnd": {"type": "integer"},
                        "title": {"type": "string"},
                        "text": {"type": "string"},
                        "message": {"type": "string"},
                        "service_name": {"type": "string"},
                        "service_action": {"type": "string", "enum": ["start", "stop", "restart"]},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def agentic_tool_definitions() -> List[Dict[str, Any]]:
    return tool_definitions() + [
        {
            "type": "function",
            "function": {
                "name": "request_user_input",
                "description": "Pause the run and ask the user for clarification or a choice.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "prompt": {"type": "string"},
                        "reason": {"type": "string"},
                        "allow_free_text": {"type": "boolean"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                    "value": {},
                                },
                                "required": ["id", "label"],
                                "additionalProperties": True,
                            },
                        },
                    },
                    "required": ["prompt"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def tool_schema_by_name(tool_name: str) -> Dict[str, Any]:
    for tool in tool_definitions():
        if tool["function"]["name"] == tool_name:
            return tool
    raise ValueError(f"unsupported tool: {tool_name}")


def task_title(tool: str, action: str, params: Dict[str, Any]) -> str:
    if tool == "shell":
        return f"Run command: {params.get('command', '')}"
    if tool == "filesystem":
        return f"{action.title()} {params.get('path') or params.get('dest') or params.get('request_id') or ''}".strip()
    if tool == "memory":
        return f"{action.title()} memory key {params.get('key', '')}".strip()
    if tool == "browser":
        target = params.get("url") or params.get("base_url") or params.get("selector") or ""
        return f"{action.replace('_', ' ').title()} {target}".strip()
    if tool == "web":
        target = params.get("query") or params.get("url") or ""
        return f"{action.replace('_', ' ').title()} {target}".strip()
    if tool == "package_manager":
        manager = params.get("manager", "package")
        package = params.get("package", "")
        return f"{action.title()} {manager} {package}".strip()
    if tool == "software_inventory":
        detail = params.get("query") or params.get("command") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    if tool == "env_manager":
        detail = params.get("name") or params.get("entry") or params.get("backup_id") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    if tool == "driver_manager":
        detail = params.get("query") or params.get("device_id") or params.get("printer_name") or params.get("path") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    if tool == "os":
        return f"OS {action.title()}"
    if tool == "desktop":
        detail = params.get("title") or params.get("service_name") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    return f"{tool}:{action}"


def normalize_agentic_task(tool_name: str, arguments: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    if tool_name == "shell":
        params = {
            "command": arguments.get("command", ""),
            "shell": arguments.get("shell", "powershell"),
        }
        if arguments.get("timeout") is not None:
            params["timeout"] = arguments["timeout"]
        action = "run"
    elif tool_name == "filesystem":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"path", "content", "dest", "pattern", "template", "request_id"} and value is not None}
    elif tool_name == "memory":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"key", "value"}}
    elif tool_name == "browser":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"url", "base_url", "query_selector", "query", "result_selector", "extractors", "actions", "selector", "file_path", "headless", "browser", "timeout"}
            and value is not None
        }
    elif tool_name == "web":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"query", "url", "download_dir", "checksum", "algorithm", "candidates"}
            and value is not None
        }
    elif tool_name == "package_manager":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"manager", "package", "version", "source"} and value is not None}
    elif tool_name == "software_inventory":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"query", "command"} and value is not None}
    elif tool_name == "env_manager":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"name", "value", "scope", "entry", "backup_id"} and value is not None}
    elif tool_name == "driver_manager":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"query", "device_id", "path", "printer_name"} and value is not None}
    elif tool_name == "os":
        action = arguments.get("action")
        params = {}
    elif tool_name == "desktop":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"hwnd", "title", "text", "message", "service_name", "service_action"}
            and value is not None
        }
    else:
        raise ValueError(f"unsupported tool call: {tool_name}")

    return {
        "id": task_id,
        "title": task_title(tool_name, action, params),
        "tool": tool_name,
        "action": action,
        "params": params,
        "depends_on": [],
        "status": "pending",
        "attempt": 0,
        "max_attempts": 2,
        "recovery": None,
        "requires_approval": False,
        "approval_id": None,
        "result": None,
        "error": None,
        "reflection": None,
    }


def to_openai_tool_call(tool_call_id: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": tool_call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }
