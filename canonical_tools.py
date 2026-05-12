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
    "windows_ui",
    "share_discovery",
    "document_intelligence",
    "office",
]

DEFAULT_ACTION_METADATA: Dict[str, Any] = {
    "semantic_type": "inspection",
    "mutates_state": False,
    "approval_mode": "none",
    "double_confirm": False,
    "reversibility": "none",
    "target_fields": [],
    "effect_kind": None,
}

ACTION_METADATA: Dict[str, Dict[str, Any]] = {
    "shell": {
        "*": {
            "semantic_type": "command",
            "mutates_state": False,
            "approval_mode": "none",
            "reversibility": "depends",
            "effect_kind": "run_command",
            "target_fields": ["command"],
        }
    },
    "filesystem": {
        "read": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "read_file"},
        "list": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "list_directory"},
        "stat": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "stat_path"},
        "find_files": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path", "pattern"], "effect_kind": "find_files"},
        "write": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path"], "effect_kind": "write_file", "reversibility": "rollback_if_available"},
        "delete": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "double", "double_confirm": True, "target_fields": ["path"], "effect_kind": "delete_file", "reversibility": "rollback_if_available"},
        "move": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path", "dest"], "effect_kind": "move_file", "reversibility": "rollback_if_available"},
        "copy": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path", "dest"], "effect_kind": "copy_file", "reversibility": "repeatable"},
        "mkdir": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path"], "effect_kind": "create_directory", "reversibility": "manual"},
        "organize_directory": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path"], "effect_kind": "organize_directory", "reversibility": "rollback_if_available"},
        "organize_downloads": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "effect_kind": "organize_downloads", "reversibility": "rollback_if_available"},
        "organize_desktop": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "effect_kind": "organize_desktop", "reversibility": "rollback_if_available"},
        "detect_duplicates": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "detect_duplicates"},
        "clean_temp": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "double", "double_confirm": True, "target_fields": ["path"], "effect_kind": "clean_temp", "reversibility": "partial"},
        "create_structure": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path"], "effect_kind": "create_structure", "reversibility": "manual"},
        "undo": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["request_id"], "effect_kind": "undo_filesystem", "reversibility": "n/a"},
    },
    "memory": {
        "get": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["key"], "effect_kind": "memory_get"},
        "set": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["key"], "effect_kind": "memory_set", "reversibility": "overwrite"},
        "delete": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "double", "double_confirm": True, "target_fields": ["key"], "effect_kind": "memory_delete", "reversibility": "none"},
        "list": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["entity_type"], "effect_kind": "memory_list"},
        "upsert_entity": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["entity_type", "key"], "effect_kind": "memory_upsert_entity", "reversibility": "overwrite"},
        "query_entities": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["entity_type", "query"], "effect_kind": "memory_query_entities"},
        "record_observation": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["entity_type", "key"], "effect_kind": "memory_record_observation", "reversibility": "overwrite"},
    },
    "browser": {
        "open_page": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["url"], "effect_kind": "open_page"},
        "search": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["base_url", "query"], "effect_kind": "browser_search"},
        "scrape_structured": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["url"], "effect_kind": "scrape_structured"},
        "download": {"semantic_type": "transfer", "mutates_state": True, "approval_mode": "single", "target_fields": ["url"], "effect_kind": "browser_download", "reversibility": "manual"},
        "interact_dom": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["url"], "effect_kind": "dom_interaction", "reversibility": "unknown"},
        "upload_file": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["url", "file_path"], "effect_kind": "upload_file", "reversibility": "unknown"},
        "bridge_native_dialog": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["title", "process_name"], "effect_kind": "bridge_native_dialog"},
    },
    "web": {
        "search_web": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query"], "effect_kind": "web_search"},
        "fetch_readonly": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["url"], "effect_kind": "fetch_page"},
        "extract_links": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["url"], "effect_kind": "extract_links"},
        "check_url": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["url"], "effect_kind": "check_url"},
        "summarize_candidates": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "summarize_candidates"},
        "download_verified": {"semantic_type": "transfer", "mutates_state": True, "approval_mode": "single", "target_fields": ["url", "download_dir"], "effect_kind": "download_verified", "reversibility": "manual"},
    },
    "package_manager": {
        "list": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_packages"},
        "search": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["package"], "effect_kind": "search_package"},
        "show": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["package"], "effect_kind": "show_package"},
        "install": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["package"], "effect_kind": "install_package", "reversibility": "uninstall"},
        "upgrade": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["package"], "effect_kind": "upgrade_package", "reversibility": "downgrade_if_available"},
        "uninstall": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "double", "double_confirm": True, "target_fields": ["package"], "effect_kind": "uninstall_package", "reversibility": "reinstall"},
    },
    "software_inventory": {
        "*": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query", "command"], "effect_kind": "software_inventory"},
    },
    "env_manager": {
        "get": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["name"], "effect_kind": "env_get"},
        "list_path_entries": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "path_list"},
        "backup": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "env_backup"},
        "set": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["name"], "effect_kind": "env_set", "reversibility": "restore"},
        "unset": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "double", "double_confirm": True, "target_fields": ["name"], "effect_kind": "env_unset", "reversibility": "restore"},
        "append_path": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["entry"], "effect_kind": "path_append", "reversibility": "restore"},
        "remove_path": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "double", "double_confirm": True, "target_fields": ["entry"], "effect_kind": "path_remove", "reversibility": "restore"},
        "restore": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["backup_id"], "effect_kind": "env_restore", "reversibility": "n/a"},
    },
    "driver_manager": {
        "list_devices": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_devices"},
        "device_status": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query", "device_id", "printer_name"], "effect_kind": "device_status"},
        "list_drivers": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_drivers"},
        "find_driver_candidates": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query", "device_id", "printer_name"], "effect_kind": "find_driver_candidates"},
        "printer_status": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query", "printer_name"], "effect_kind": "printer_status"},
        "printer_driver_info": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query", "printer_name"], "effect_kind": "printer_driver_info"},
        "install_inf": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path"], "effect_kind": "install_driver", "reversibility": "rollback_if_available"},
        "add_driver_package": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path"], "effect_kind": "add_driver_package", "reversibility": "rollback_if_available"},
        "rollback_driver": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "double", "double_confirm": True, "target_fields": ["query", "device_id"], "effect_kind": "rollback_driver", "reversibility": "n/a"},
        "scan_hardware_changes": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "effect_kind": "scan_hardware_changes", "reversibility": "n/a"},
        "restart_spooler": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "effect_kind": "restart_spooler", "reversibility": "n/a"},
    },
    "os": {
        "*": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "os_inspection"},
    },
    "desktop": {
        "list_processes": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_processes"},
        "list_windows": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_windows"},
        "list_explorer_windows": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_explorer_windows"},
        "list_mapped_drives": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_mapped_drives"},
        "get_explorer_selection": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "get_explorer_selection"},
        "clipboard_get": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "clipboard_get"},
        "list_services": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_services"},
        "open_app": {"semantic_type": "execution", "mutates_state": False, "approval_mode": "none", "target_fields": ["app_name", "app_path"], "effect_kind": "open_app", "reversibility": "close_window"},
        "open_path": {"semantic_type": "execution", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "open_path", "reversibility": "close_window"},
        "open_file": {"semantic_type": "execution", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "open_file", "reversibility": "close_window"},
        "wait_for_window": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["title", "process_name", "hwnd"], "effect_kind": "wait_for_window"},
        "focus_window": {"semantic_type": "execution", "mutates_state": False, "approval_mode": "none", "target_fields": ["title", "hwnd"], "effect_kind": "focus_window", "reversibility": "n/a"},
        "close_window": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["title", "hwnd"], "effect_kind": "close_window", "reversibility": "reopen_if_possible"},
        "kill_process": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["pid", "process_name", "title"], "effect_kind": "kill_process", "reversibility": "restart_if_possible"},
        "clipboard_set": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["text"], "effect_kind": "clipboard_set", "reversibility": "overwrite"},
        "notify": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["message"], "effect_kind": "notify", "reversibility": "n/a"},
        "service_action": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["service_name", "service_action"], "effect_kind": "service_action", "reversibility": "depends"},
    },
    "windows_ui": {
        "inspect_window": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["hwnd", "title", "process_name"], "effect_kind": "inspect_window"},
        "list_elements": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["hwnd", "title", "process_name", "selector"], "effect_kind": "list_elements"},
        "find_element": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["hwnd", "title", "process_name", "selector", "element_id"], "effect_kind": "find_element"},
        "wait_for_element": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["hwnd", "title", "process_name", "selector", "element_id"], "effect_kind": "wait_for_element"},
        "invoke_element": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["hwnd", "title", "process_name", "selector", "element_id"], "effect_kind": "invoke_element", "reversibility": "depends"},
        "set_text": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["hwnd", "title", "process_name", "selector", "element_id", "text"], "effect_kind": "set_text", "reversibility": "overwrite"},
        "select_item": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["hwnd", "title", "process_name", "selector", "element_id", "item_text"], "effect_kind": "select_item", "reversibility": "depends"},
        "send_hotkey": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["hotkey"], "effect_kind": "send_hotkey", "reversibility": "depends"},
        "scroll": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["direction", "amount", "hwnd", "title", "process_name", "selector", "element_id"], "effect_kind": "scroll", "reversibility": "depends"},
        "read_element_text": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["hwnd", "title", "process_name", "selector", "element_id"], "effect_kind": "read_element_text"},
        "get_focused_element": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "get_focused_element"},
    },
    "share_discovery": {
        "*": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path", "query"], "effect_kind": "share_discovery"},
    },
    "document_intelligence": {
        "*": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path", "root", "query"], "effect_kind": "document_intelligence"},
    },
    "office": {
        "open_document": {"semantic_type": "execution", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "open_document"},
        "export_pdf": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path", "output_path"], "effect_kind": "export_pdf", "reversibility": "delete_output"},
        "save_as_document": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path", "output_path"], "effect_kind": "save_as_document", "reversibility": "delete_output"},
        "list_workbook_sheets": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "list_workbook_sheets"},
        "draft_email_with_attachment": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["to", "attachment_path"], "effect_kind": "draft_email_with_attachment", "reversibility": "discard_draft"},
        "reveal_active_document_path": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["app_name"], "effect_kind": "reveal_active_document_path"},
    },
}


def supported_tools() -> List[str]:
    return list(SUPPORTED_TOOL_NAMES)


def action_metadata(tool: str, action: str) -> Dict[str, Any]:
    metadata = dict(DEFAULT_ACTION_METADATA)
    tool_metadata = ACTION_METADATA.get(tool, {})
    if "*" in tool_metadata:
        metadata.update(tool_metadata["*"])
    if action in tool_metadata:
        metadata.update(tool_metadata[action])
    return metadata


def tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "Execute arbitrary Windows PowerShell or cmd commands for discovery, diagnostics, mapped drives, network shares, Explorer-related inspection, and controlled automation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to execute."},
                        "shell": {"type": "string", "enum": ["powershell", "cmd"]},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 180},
                        "retries": {"type": "integer", "minimum": 1, "maximum": 5},
                        "require_admin": {"type": "boolean"},
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
                        "action": {"type": "string", "enum": ["set", "get", "delete", "list", "upsert_entity", "query_entities", "record_observation"]},
                        "key": {"type": "string"},
                        "value": {"type": "object"},
                        "entity_type": {"type": "string"},
                        "attributes": {"type": "object"},
                        "query": {"type": "string"},
                    },
                    "required": ["action"],
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
                            "enum": ["open_page", "search", "download", "scrape_structured", "interact_dom", "upload_file", "bridge_native_dialog"],
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
                        "title": {"type": "string"},
                        "process_name": {"type": "string"},
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
                "description": "Use Windows-native desktop primitives for discovery and control, including Explorer paths, mapped drives, opening apps/files/folders, waiting for windows, and process control.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "list_processes",
                                "list_windows",
                                "list_explorer_windows",
                                "list_mapped_drives",
                                "get_explorer_selection",
                                "open_app",
                                "open_path",
                                "open_file",
                                "wait_for_window",
                                "focus_window",
                                "close_window",
                                "kill_process",
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
                        "path": {"type": "string"},
                        "app_name": {"type": "string"},
                        "app_path": {"type": "string"},
                        "arguments": {"type": "array", "items": {"type": "string"}},
                        "process_name": {"type": "string"},
                        "pid": {"type": "integer"},
                        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                        "service_name": {"type": "string"},
                        "service_action": {"type": "string", "enum": ["start", "stop", "restart"]},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "windows_ui",
                "description": "Inspect and operate native Windows UI controls using UI Automation rather than pixel-based automation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "inspect_window",
                                "list_elements",
                                "find_element",
                                "wait_for_element",
                                "invoke_element",
                                "set_text",
                                "select_item",
                                "send_hotkey",
                                "scroll",
                                "read_element_text",
                                "get_focused_element",
                            ],
                        },
                        "hwnd": {"type": "integer"},
                        "title": {"type": "string"},
                        "process_name": {"type": "string"},
                        "selector": {"type": "object"},
                        "element_id": {"type": "string"},
                        "text": {"type": "string"},
                        "item_text": {"type": "string"},
                        "hotkey": {"type": "string"},
                        "direction": {"type": "string", "enum": ["up", "down"]},
                        "amount": {"type": "integer", "minimum": 1, "maximum": 20},
                        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                        "include_children": {"type": "boolean"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "share_discovery",
                "description": "Discover mapped drives, Explorer network context and inspect share paths through typed Windows-native queries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list_mappings", "list_explorer_context", "inspect_share", "discover_targets"]},
                        "path": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "document_intelligence",
                "description": "Inspect documents, extract text, search content and list recent document candidates across Office, PDF and mail formats.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["inspect_document", "extract_text", "search_content", "recent_documents"]},
                        "path": {"type": "string"},
                        "root": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "office",
                "description": "Use Office COM automation for Word, Excel and Outlook workflows with native fallbacks handled by other tools.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "open_document",
                                "export_pdf",
                                "save_as_document",
                                "list_workbook_sheets",
                                "draft_email_with_attachment",
                                "reveal_active_document_path",
                            ],
                        },
                        "path": {"type": "string"},
                        "output_path": {"type": "string"},
                        "app_name": {"type": "string"},
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "attachment_path": {"type": "string"},
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
        detail = params.get("key") or params.get("entity_type") or params.get("query") or ""
        return f"{action.title()} memory {detail}".strip()
    if tool == "browser":
        target = params.get("url") or params.get("base_url") or params.get("selector") or params.get("title") or params.get("process_name") or ""
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
        detail = (
            params.get("app_name")
            or params.get("path")
            or params.get("title")
            or params.get("process_name")
            or params.get("service_name")
            or params.get("pid")
            or ""
        )
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    if tool == "windows_ui":
        detail = params.get("title") or params.get("process_name") or params.get("element_id") or params.get("hotkey") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    if tool == "share_discovery":
        detail = params.get("path") or params.get("query") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    if tool == "document_intelligence":
        detail = params.get("path") or params.get("root") or params.get("query") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    if tool == "office":
        detail = params.get("path") or params.get("app_name") or params.get("output_path") or params.get("attachment_path") or ""
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
        if arguments.get("retries") is not None:
            params["retries"] = arguments["retries"]
        if arguments.get("require_admin") is not None:
            params["require_admin"] = bool(arguments["require_admin"])
        action = "run"
    elif tool_name == "filesystem":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"path", "content", "dest", "pattern", "template", "request_id"} and value is not None}
    elif tool_name == "memory":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"key", "value", "entity_type", "attributes", "query"} and value is not None}
    elif tool_name == "browser":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"url", "base_url", "query_selector", "query", "result_selector", "extractors", "actions", "selector", "file_path", "headless", "browser", "timeout", "title", "process_name"}
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
            if key in {
                "hwnd",
                "title",
                "text",
                "message",
                "path",
                "app_name",
                "app_path",
                "arguments",
                "process_name",
                "pid",
                "timeout_seconds",
                "service_name",
                "service_action",
            }
            and value is not None
        }
    elif tool_name == "windows_ui":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"hwnd", "title", "process_name", "selector", "element_id", "text", "item_text", "hotkey", "direction", "amount", "timeout_seconds", "max_results", "include_children"}
            and value is not None
        }
    elif tool_name == "share_discovery":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"path", "query", "limit"} and value is not None}
    elif tool_name == "document_intelligence":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"path", "root", "query", "limit"} and value is not None}
    elif tool_name == "office":
        action = arguments.get("action")
        params = {key: value for key, value in arguments.items() if key in {"path", "output_path", "app_name", "to", "subject", "body", "attachment_path"} and value is not None}
    else:
        raise ValueError(f"unsupported tool call: {tool_name}")

    return {
        "id": task_id,
        "title": task_title(tool_name, action, params),
        "tool": tool_name,
        "action": action,
        "params": params,
        "intent": {
            "tool": tool_name,
            "action": action,
            "params": params,
            "task_id": task_id,
        },
        "policy_metadata": action_metadata(tool_name, action),
        "depends_on": [],
        "status": "pending",
        "attempt": 0,
        "max_attempts": 2,
        "recovery": None,
        "requires_approval": False,
        "approval_granted": False,
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
