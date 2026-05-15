# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
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
    "sandbox",
    "artifact",
    "dynamic_tool",
    "visual",
    "skill",
    "codebase_map",
    "execution_economics",
    "test_diagnostic",
    "patch_planner",
    "heartbeat",
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
        "world_upsert": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["entity_type", "key"], "effect_kind": "world_upsert", "reversibility": "overwrite"},
        "world_get": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["entity_type", "key"], "effect_kind": "world_get"},
        "world_query": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["entity_type", "query"], "effect_kind": "world_query"},
        "world_delete": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["entity_type", "key"], "effect_kind": "world_delete", "reversibility": "none"},
        "world_touch": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["entity_type", "key"], "effect_kind": "world_touch", "reversibility": "overwrite"},
        "world_prune": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["entity_type"], "effect_kind": "world_prune", "reversibility": "none"},
        "world_types": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "world_types"},
        "world_remember_share": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["key"], "effect_kind": "world_remember_share", "reversibility": "overwrite"},
        "world_remember_app": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["key"], "effect_kind": "world_remember_app", "reversibility": "overwrite"},
        "world_remember_alias": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["key"], "effect_kind": "world_remember_alias", "reversibility": "overwrite"},
        "world_remember_selector": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["key"], "effect_kind": "world_remember_selector", "reversibility": "overwrite"},
        "world_remember_path": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["key"], "effect_kind": "world_remember_path", "reversibility": "overwrite"},
        "world_find_share": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query"], "effect_kind": "world_find_share"},
        "world_find_app": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query"], "effect_kind": "world_find_app"},
        "world_find_alias": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query"], "effect_kind": "world_find_alias"},
        "world_find_selector": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query"], "effect_kind": "world_find_selector"},
        "world_find_path": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query"], "effect_kind": "world_find_path"},
        "strategy_record_success": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["goal_type", "strategy_id"], "effect_kind": "strategy_record_success", "reversibility": "overwrite"},
        "strategy_record_failure": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["goal_type"], "effect_kind": "strategy_record_failure", "reversibility": "overwrite"},
        "strategy_find": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["goal_type", "query"], "effect_kind": "strategy_find"},
        "strategy_best": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["goal_type"], "effect_kind": "strategy_best"},
        "strategy_reused": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["goal_type", "strategy_id"], "effect_kind": "strategy_reused", "reversibility": "overwrite"},
        "strategy_goal_types": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "strategy_goal_types"},
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
        "printer_diagnostics": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query", "printer_name"], "effect_kind": "printer_diagnostics"},
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
        "explorer_context": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "explorer_context"},
        "explorer_select_path": {"semantic_type": "execution", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "explorer_select_path", "reversibility": "close_window"},
        "explorer_navigate": {"semantic_type": "execution", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "explorer_navigate", "reversibility": "close_window"},
        "explorer_rename_path": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path", "new_name"], "effect_kind": "explorer_rename_path", "reversibility": "rename_back"},
        "explorer_copy_path": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path", "dest"], "effect_kind": "explorer_copy_path", "reversibility": "delete_output"},
        "explorer_move_path": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["path", "dest"], "effect_kind": "explorer_move_path", "reversibility": "move_back"},
        "inspect_installer": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "inspect_installer"},
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
        "inspect_dialog": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["hwnd", "title", "process_name"], "effect_kind": "inspect_dialog"},
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
        "word_find_text": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path", "query"], "effect_kind": "word_find_text"},
        "excel_read_range": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path", "sheet_name", "range_address"], "effect_kind": "excel_read_range"},
        "outlook_search_messages": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query"], "effect_kind": "outlook_search_messages"},
        "outlook_read_latest": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["limit", "folder"], "effect_kind": "outlook_read_latest"},
        "word_create_document": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["output_path", "content"], "effect_kind": "word_create_document", "reversibility": "delete_file"},
        "draft_email_with_attachment": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["to", "attachment_path"], "effect_kind": "draft_email_with_attachment", "reversibility": "discard_draft"},
        "reveal_active_document_path": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["app_name"], "effect_kind": "reveal_active_document_path"},
    },
    "sandbox": {
        "execute_python": {"semantic_type": "execution", "mutates_state": True, "approval_mode": "single", "target_fields": ["code"], "effect_kind": "execute_python_script", "reversibility": "none"},
        "execute_powershell": {"semantic_type": "execution", "mutates_state": True, "approval_mode": "single", "target_fields": ["code"], "effect_kind": "execute_powershell_script", "reversibility": "none"},
    },
    "artifact": {
        "load": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "load_artifact"},
        "transform": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path", "operation"], "effect_kind": "transform_artifact"},
        "write_result": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["output_path"], "effect_kind": "write_artifact_result", "reversibility": "delete_output"},
    },
    "dynamic_tool": {
        "create": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["name", "code"], "effect_kind": "create_dynamic_tool", "reversibility": "delete_tool"},
        "list": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_dynamic_tools"},
        "get": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["tool_id"], "effect_kind": "get_dynamic_tool"},
        "execute": {"semantic_type": "execution", "mutates_state": True, "approval_mode": "single", "target_fields": ["tool_id"], "effect_kind": "execute_dynamic_tool", "reversibility": "none"},
        "delete": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "double", "double_confirm": True, "target_fields": ["tool_id"], "effect_kind": "delete_dynamic_tool", "reversibility": "none"},
    },
    "skill": {
        "register": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["name", "version"], "effect_kind": "register_skill", "reversibility": "unregister"},
        "update": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["name", "version"], "effect_kind": "update_skill", "reversibility": "rollback_version"},
        "unregister": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "double", "double_confirm": True, "target_fields": ["name"], "effect_kind": "unregister_skill", "reversibility": "none"},
        "get": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["name"], "effect_kind": "get_skill"},
        "list": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_skills"},
        "search": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query"], "effect_kind": "search_skills"},
        "discover_objective": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["objective"], "effect_kind": "discover_skills_by_objective"},
        "execute": {"semantic_type": "execution", "mutates_state": True, "approval_mode": "single", "target_fields": ["name"], "effect_kind": "execute_skill", "reversibility": "none"},
        "verify": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["name"], "effect_kind": "verify_skill"},
        "verify_trust": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["name"], "effect_kind": "verify_skill_trust"},
        "approve_trust": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["name"], "effect_kind": "approve_skill_trust", "reversibility": "quarantine"},
        "validate_manifest": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "validate_manifest"},
        "upgrade_dynamic": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "single", "target_fields": ["tool_id"], "effect_kind": "upgrade_dynamic_tool", "reversibility": "unregister"},
    },
    "visual": {
        "capture_screen": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "capture_screen"},
        "capture_window": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["hwnd", "title"], "effect_kind": "capture_window"},
        "analyze_elements": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "analyze_elements"},
        "ground_element": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["text", "title", "process_name"], "effect_kind": "ground_element"},
        "verify_action": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["hwnd", "title"], "effect_kind": "verify_action"},
        "evaluate_fallback": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["tool", "target_action"], "effect_kind": "evaluate_fallback"},
    },
    "codebase_map": {
        "scan": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["workspace"], "effect_kind": "scan_codebase"},
        "stats": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "codebase_stats"},
        "find_symbol": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["name", "kind"], "effect_kind": "find_symbol"},
        "find_imports": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["module"], "effect_kind": "find_imports"},
        "find_tests": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["module"], "effect_kind": "find_tests"},
        "find_routes": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path_pattern"], "effect_kind": "find_routes"},
        "find_configs": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["config_type"], "effect_kind": "find_configs"},
        "find_owners": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["owner"], "effect_kind": "find_owners"},
        "get_file": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "get_file_map"},
        "dependency_graph": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["path"], "effect_kind": "dependency_graph"},
        "search": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["query"], "effect_kind": "search_codebase"},
    },
    "execution_economics": {
        "create_tracker": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["run_id", "model"], "effect_kind": "create_cost_tracker"},
        "record_step": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["run_id", "tool"], "effect_kind": "record_step_cost"},
        "record_llm_usage": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["run_id"], "effect_kind": "record_llm_usage"},
        "get_summary": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["run_id"], "effect_kind": "cost_summary"},
        "check_budget": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["run_id"], "effect_kind": "check_budget"},
        "analyze_complexity": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "analyze_complexity"},
        "evaluate_stopping": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["run_id"], "effect_kind": "evaluate_stopping"},
        "rank_strategies": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "rank_strategies"},
        "compare_routes": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "compare_routes"},
        "best_route": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "best_route"},
        "record_tool_outcome": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["tool", "tool_action", "status"], "effect_kind": "record_tool_outcome"},
        "get_tool_confidence": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["tool", "tool_action"], "effect_kind": "get_tool_confidence"},
        "list_tool_confidences": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["tool"], "effect_kind": "list_tool_confidences"},
        "plan_tool_confidence": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "plan_tool_confidence"},
        "replay_regression": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["request_id"], "effect_kind": "replay_regression"},
        "list_replayable_runs": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_replayable_runs"},
        "get_autonomy_metrics": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["request_id"], "effect_kind": "get_autonomy_metrics"},
        "list_autonomy_metrics": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_autonomy_metrics"},
        "get_quality_dashboard": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "get_quality_dashboard"},
        "list_quality_versions": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "list_quality_versions"},
    },
    "test_diagnostic": {
        "run_and_diagnose": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["target"], "effect_kind": "run_and_diagnose_tests"},
        "start_session": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["target"], "effect_kind": "start_diagnostic_session"},
        "iterate": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["session_id"], "effect_kind": "diagnostic_iteration"},
        "get_session": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["session_id"], "effect_kind": "get_diagnostic_session"},
    },
    "patch_planner": {
        "plan": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "create_patch_plan"},
        "assess_risk": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "assess_change_risk"},
        "order_changes": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "order_changes"},
    },
    "heartbeat": {
        "start_heartbeat": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["tool_name", "run_id"], "effect_kind": "start_heartbeat"},
        "stop_heartbeat": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["tool_name", "run_id"], "effect_kind": "stop_heartbeat"},
        "heartbeat_status": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["tool_name", "run_id"], "effect_kind": "heartbeat_status"},
        "touch": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["tool_name", "run_id"], "effect_kind": "heartbeat_touch"},
        "start_progress": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["tool_name", "run_id"], "effect_kind": "start_progress"},
        "update_progress": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["tool_name", "run_id"], "effect_kind": "update_progress"},
        "complete_progress": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["tool_name", "run_id"], "effect_kind": "complete_progress"},
        "progress_status": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["tool_name", "run_id"], "effect_kind": "progress_status"},
        "add_watch_target": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["pid", "hwnd", "process_name"], "effect_kind": "add_watch_target"},
        "remove_watch_target": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "target_fields": ["target_key"], "effect_kind": "remove_watch_target"},
        "start_watchdog": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "effect_kind": "start_watchdog"},
        "stop_watchdog": {"semantic_type": "mutation", "mutates_state": True, "approval_mode": "none", "effect_kind": "stop_watchdog"},
        "check_frozen": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "target_fields": ["hwnd", "pid", "title_pattern"], "effect_kind": "check_frozen"},
        "watchdog_status": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "watchdog_status"},
        "check_once": {"semantic_type": "inspection", "mutates_state": False, "approval_mode": "none", "effect_kind": "watchdog_check_once"},
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
                "description": "Store, retrieve and query structured memory, typed world model entities (shares, app paths, document aliases, selectors, path candidates) with confidence and expiration, and strategy history per goal type.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "set", "get", "delete", "list", "upsert_entity", "query_entities", "record_observation",
                                "world_upsert", "world_get", "world_query", "world_delete", "world_touch", "world_prune", "world_types",
                                "world_remember_share", "world_remember_app", "world_remember_alias", "world_remember_selector", "world_remember_path",
                                "world_find_share", "world_find_app", "world_find_alias", "world_find_selector", "world_find_path",
                                "strategy_record_success", "strategy_record_failure", "strategy_find", "strategy_best", "strategy_reused", "strategy_goal_types",
                            ],
                        },
                        "key": {"type": "string"},
                        "value": {"type": "object"},
                        "entity_type": {"type": "string", "description": "Entity type (share, app_path, document_alias, selector, path_candidate, device, web_resource, user_preference, environment)"},
                        "attributes": {"type": "object"},
                        "query": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Confidence score for the entity (0.0-1.0)"},
                        "source": {"type": "string", "description": "Source of the entity (discovery, user, ui_automation, etc.)"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for filtering"},
                        "ttl_seconds": {"type": "integer", "minimum": 1, "description": "Time-to-live in seconds (overrides entity type default)"},
                        "app_context": {"type": "string", "description": "Application context for selectors"},
                        "min_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Minimum confidence filter for queries"},
                        "boost_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Amount to boost confidence on touch"},
                        "strategy_id": {"type": "string", "description": "Strategy identifier for strategy_record_success/strategy_reused"},
                        "goal_type": {"type": "string", "description": "Goal type for strategy actions (e.g. open_document, install_software, find_file)"},
                        "goal_summary": {"type": "string", "description": "Human-readable summary of the goal"},
                        "steps": {"type": "array", "items": {"type": "object"}, "description": "Tool call steps that form the strategy"},
                        "error": {"type": "string", "description": "Error description for strategy_record_failure"},
                        "duration_ms": {"type": "integer", "description": "Execution duration in milliseconds"},
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
                "description": "Use safe web research and validated downloads. For unfamiliar technical procedures, use search_web as autonomous discovery, prefer official docs, Microsoft Learn, StackOverflow/SuperUser/ServerFault and vendor sources, then apply the result instead of only reporting links.",
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
                                "printer_diagnostics",
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
                                "explorer_context",
                                "explorer_select_path",
                                "explorer_navigate",
                                "explorer_rename_path",
                                "explorer_copy_path",
                                "explorer_move_path",
                                "inspect_installer",
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
                        "dest": {"type": "string"},
                        "new_name": {"type": "string"},
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
                                "inspect_dialog",
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
                        "action": {"type": "string", "enum": ["list_mappings", "list_explorer_context", "inspect_share", "inspect_corporate_share", "discover_targets"]},
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
                "description": "Inspect documents, OCR scanned PDFs/images, index/search local content, filter by metadata, and discover contracts, invoices, emails and attachments.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "inspect_document",
                                "extract_text",
                                "search_content",
                                "index_documents",
                                "search_index",
                                "discover_documents",
                                "recent_documents",
                            ],
                        },
                        "path": {"type": "string"},
                        "root": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                        "filters": {"type": "object"},
                        "use_ocr": {"type": "boolean"},
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
                "description": "Use Office COM automation for Word, Excel and Outlook workflows. All actions run headlessly in the background — no need for the user to have any Office app open. Use outlook_read_latest to read recent emails, word_create_document to create Word docs, outlook_search_messages to search emails by keyword.",
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
                                "word_find_text",
                                "excel_read_range",
                                "outlook_search_messages",
                                "outlook_read_latest",
                                "word_create_document",
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
                        "content": {"type": "string", "description": "Text content for word_create_document"},
                        "title": {"type": "string", "description": "Optional document title for word_create_document"},
                        "attachment_path": {"type": "string"},
                        "query": {"type": "string"},
                        "sheet_name": {"type": "string"},
                        "range_address": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                        "folder": {"type": "string", "enum": ["inbox", "sent", "drafts", "outbox"], "description": "Outlook folder for outlook_read_latest"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sandbox",
                "description": "Execute dynamic Python or PowerShell scripts in a controlled sandbox environment. Use this when no existing tool covers the task, or when the user needs custom data analysis, file transformation, automation scripts, or ad-hoc computations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["execute_python", "execute_powershell"]},
                        "code": {"type": "string", "description": "Script source code to execute"},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
                        "work_dir": {"type": "string", "description": "Working directory override"},
                        "input_files": {"type": "object", "description": "Files to create in sandbox before execution (name -> content)"},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Declared capabilities (filesystem:read, network:outbound, shell:run, etc.)",
                        },
                        "request_id": {"type": "string", "description": "Run id — binds run filesystem scope when work_dir omitted"},
                    },
                    "required": ["action", "code"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "artifact",
                "description": "Load, read, transform and analyze files internally without opening them on the user's desktop. Supports text, CSV, JSON, PDF, DOCX, XLSX and MSG formats. Use this to process artifacts in memory and return results directly.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["load", "transform", "write_result"]},
                        "path": {"type": "string", "description": "Path to file to load or transform"},
                        "operation": {"type": "string", "enum": ["summarize", "extract_table", "filter_lines", "convert_json", "stats"], "description": "Transform operation"},
                        "params": {"type": "object", "description": "Operation-specific parameters (e.g. pattern for filter_lines)"},
                        "content": {"type": "string", "description": "Content to write (for write_result)"},
                        "output_path": {"type": "string", "description": "Output file path (for write_result)"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dynamic_tool",
                "description": "Create, manage and execute dynamic micro-tools. Use this to write and persist small purpose-built tools during a run to handle scenarios not covered by native tools. Created tools are reusable across runs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["create", "list", "get", "execute", "delete"]},
                        "name": {"type": "string", "description": "Tool name (for create)"},
                        "description": {"type": "string", "description": "Tool description (for create)"},
                        "code": {"type": "string", "description": "Tool source code with a run(params) function (for create)"},
                        "language": {"type": "string", "enum": ["python", "powershell"], "description": "Language (for create)"},
                        "tool_id": {"type": "string", "description": "Tool ID (for execute, delete, get)"},
                        "params": {"type": "object", "description": "Execution parameters (for execute)"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags (for create)"},
                        "tag": {"type": "string", "description": "Filter by tag (for list)"},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 300, "description": "Execution timeout (for execute)"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "skill",
                "description": "Manage the local skill manifest system. ALWAYS call discover_objective with the user's goal before creating sandbox scripts or dynamic tools. Register, update, query, verify signature/trust, approve altered imports, and execute versioned skills with declared permissions and risk profiles.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["register", "update", "unregister", "get", "list", "search", "discover_objective", "execute", "verify", "verify_trust", "approve_trust", "validate_manifest", "upgrade_dynamic", "evaluate", "export_package", "import_package"],
                        },
                        "name": {"type": "string", "description": "Skill name (lowercase, a-z0-9_.-) for register/update/get/execute/verify"},
                        "version": {"type": "string", "description": "Semver version (X.Y.Z)"},
                        "display_name": {"type": "string", "description": "Human-readable name"},
                        "description": {"type": "string", "description": "What the skill does"},
                        "author": {"type": "string"},
                        "language": {"type": "string", "enum": ["python", "powershell"]},
                        "code": {"type": "string", "description": "Skill source code with a run(params) function"},
                        "permissions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Required capabilities (e.g. filesystem:read, shell:run, network:outbound)",
                        },
                        "inputs": {
                            "type": "object",
                            "description": "Input schema: {params: [{name, type, description, required, default}], description}",
                        },
                        "outputs": {
                            "type": "object",
                            "description": "Output schema: {params: [{name, type, description}], description}",
                        },
                        "risk": {
                            "type": "object",
                            "description": "Risk profile: {level, tags, rationale, reversible, side_effects, data_exposure, requires_approval, max_execution_time_seconds}",
                        },
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Domain/category tags"},
                        "entry_point": {"type": "string", "description": "Function name to call (default: run)"},
                        "dependencies": {"type": "array", "items": {"type": "string"}, "description": "Python packages required"},
                        "params": {"type": "object", "description": "Execution parameters (for execute)"},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 3600, "description": "Execution timeout in seconds"},
                        "tag": {"type": "string", "description": "Filter by tag (for list)"},
                        "permission": {"type": "string", "description": "Filter by permission (for list)"},
                        "max_risk": {"type": "string", "enum": ["low", "medium", "high", "critical"], "description": "Max risk level filter (for list)"},
                        "query": {"type": "string", "description": "Search query (for search)"},
                        "objective": {"type": "string", "description": "Natural-language task goal (for discover_objective) — run this BEFORE new scripts"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Max matches returned (discover_objective)"},
                        "min_score": {"type": "number", "minimum": 0, "maximum": 1, "description": "Minimum relevance score (discover_objective)"},
                        "trusted_only": {"type": "boolean", "description": "Only trusted/signed skills (discover_objective)"},
                        "evaluated_only": {"type": "boolean", "description": "Only evaluation-passed skills (discover_objective, default true)"},
                        "agent_available_only": {"type": "boolean", "description": "Only agent-ready skills (list)"},
                        "package_path": {"type": "string", "description": "Folder or .phylum-skillpack path (export_package/import_package)"},
                        "skill_names": {"type": "array", "items": {"type": "string"}, "description": "Skills to include in package (export_package)"},
                        "as_zip": {"type": "boolean", "description": "Export as .phylum-skillpack zip (export_package)"},
                        "overwrite": {"type": "boolean", "description": "Overwrite existing skills on import"},
                        "run_evaluation": {"type": "boolean", "description": "Run tests after register/import"},
                        "tests": {"type": "array", "items": {"type": "object"}, "description": "Skill test cases [{name, params, expect}]"},
                        "min_tests": {"type": "integer", "minimum": 1, "description": "Minimum tests required in tests.json"},
                        "review_notes": {"type": "string", "description": "Notes when approving trust after review"},
                        "tool_spec": {"type": "object", "description": "DynamicToolSpec dict to upgrade (for upgrade_dynamic)"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "visual",
                "description": "Visual perception and hybrid computer-use. Capture screenshots, detect text via OCR, ground visual elements to UIA selectors, verify actions visually (before/after), and evaluate anti-fragility fallback policy. Prefer native APIs — use visual only when UIA/COM/DOM fail.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["capture_screen", "capture_window", "analyze_elements", "ground_element", "verify_action", "evaluate_fallback"],
                        },
                        "hwnd": {"type": "integer", "description": "Window handle to target"},
                        "title": {"type": "string", "description": "Window title to target"},
                        "process_name": {"type": "string", "description": "Process name for grounding context"},
                        "region": {"type": "object", "properties": {"left": {"type": "integer"}, "top": {"type": "integer"}, "right": {"type": "integer"}, "bottom": {"type": "integer"}}, "description": "Screen region for capture_screen"},
                        "text": {"type": "string", "description": "Text to search for (ground_element)"},
                        "tool": {"type": "string", "description": "Tool name for evaluate_fallback"},
                        "target_action": {"type": "string", "description": "Action name for evaluate_fallback"},
                        "native_failed": {"type": "boolean", "description": "Whether the native API path failed"},
                        "native_error": {"type": "string", "description": "Error from the native API attempt"},
                        "grounded": {"type": "boolean", "description": "Whether grounding produced a verified element"},
                        "grounding_score": {"type": "number", "description": "Grounding confidence score (0.0-1.0)"},
                        "semantic_type": {"type": "string", "description": "Semantic type of the action"},
                        "mutates_state": {"type": "boolean", "description": "Whether the action mutates state"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "codebase_map",
                "description": "Persistent codebase map. Scan a workspace to extract symbols, imports, routes, tests, configs and ownership per file. Query the map to find definitions, dependencies, test coverage and file ownership. Supports incremental updates — only rescans files whose content has changed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["scan", "stats", "find_symbol", "find_imports", "find_tests",
                                     "find_routes", "find_configs", "find_owners", "get_file",
                                     "dependency_graph", "search"],
                        },
                        "workspace": {"type": "string", "description": "Workspace root path (defaults to cwd)"},
                        "name": {"type": "string", "description": "Symbol name to search for"},
                        "kind": {"type": "string", "description": "Symbol kind filter: class, function, method, constant"},
                        "module": {"type": "string", "description": "Module name for find_imports or find_tests"},
                        "path": {"type": "string", "description": "File path for get_file or dependency_graph"},
                        "path_pattern": {"type": "string", "description": "Route path pattern for find_routes"},
                        "config_type": {"type": "string", "description": "Config type filter for find_configs"},
                        "owner": {"type": "string", "description": "Owner name for find_owners"},
                        "query": {"type": "string", "description": "Search query for unified search"},
                        "force": {"type": "boolean", "description": "Force full rescan (ignore hashes)"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execution_economics",
                "description": "Execution economics — track cost per run, analyze path complexity, stopping heuristics, route optimization, per-tool/action confidence from success history, and regression replay of persisted runs in dry-run mode.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create_tracker", "record_step", "record_llm_usage",
                                     "get_summary", "check_budget", "analyze_complexity",
                                     "evaluate_stopping", "rank_strategies", "compare_routes",
                                     "best_route", "record_tool_outcome", "get_tool_confidence",
                                     "list_tool_confidences", "plan_tool_confidence",
                                     "replay_regression", "list_replayable_runs",
                                     "get_autonomy_metrics", "list_autonomy_metrics",
                                     "get_quality_dashboard", "list_quality_versions"],
                        },
                        "run_id": {"type": "string", "description": "Run identifier for cost tracking"},
                        "model": {"type": "string", "description": "LLM model name for pricing"},
                        "budget_usd": {"type": "number", "description": "USD budget limit for the run"},
                        "budget_tokens": {"type": "integer", "description": "Token budget limit for the run"},
                        "step_index": {"type": "integer", "description": "Step number"},
                        "tool": {"type": "string", "description": "Tool name for the step"},
                        "tool_action": {"type": "string", "description": "Action within the tool"},
                        "status": {"type": "string", "description": "Step outcome: succeeded, failed, retried"},
                        "prompt_tokens": {"type": "integer", "description": "Input tokens used"},
                        "completion_tokens": {"type": "integer", "description": "Output tokens used"},
                        "duration_ms": {"type": "integer", "description": "Step duration in milliseconds"},
                        "is_retry": {"type": "boolean", "description": "Whether this step is a retry"},
                        "is_replan": {"type": "boolean", "description": "Whether this step is a replan"},
                        "error": {"type": "string", "description": "Error message if step failed"},
                        "current_confidence": {"type": "number", "description": "Current goal confidence (0-1)"},
                        "goal_progress": {"type": "number", "description": "Progress toward goal (0-1)"},
                        "consecutive_errors": {"type": "integer", "description": "Number of consecutive errors"},
                        "strategies": {"type": "array", "items": {"type": "object"}, "description": "Strategy records to rank or select from"},
                        "route_a": {"type": "object", "description": "First route for comparison"},
                        "route_b": {"type": "object", "description": "Second route for comparison"},
                        "steps": {"type": "array", "items": {"type": "object"}, "description": "Step records for complexity analysis"},
                        "branch_count": {"type": "integer", "description": "Number of parallel branches"},
                        "request_id": {"type": "string", "description": "Persisted run id for regression replay"},
                        "replan": {"type": "boolean", "description": "Re-plan from original input during replay (default true)"},
                        "validate_tasks": {"type": "boolean", "description": "Validate stored tasks in dry-run (default true)"},
                        "min_samples": {"type": "integer", "description": "Minimum samples when listing confidences"},
                        "limit": {"type": "integer", "description": "Max rows for list operations"},
                        "plan_steps": {"type": "array", "items": {"type": "object"}, "description": "Tool/action pairs for plan_tool_confidence"},
                        "runtime_mode": {"type": "string", "description": "Filter dashboard by runtime mode (agentic, heuristic, etc.)"},
                        "provider": {"type": "string", "description": "Filter dashboard by LLM provider"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "test_diagnostic",
                "description": "Test diagnostic loop — run tests, interpret failures (extract error type, source location, root cause), suggest fixes, rerun to verify, and expand to regression tests. Supports pytest, jest and unittest.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["run_and_diagnose", "start_session", "iterate", "get_session"],
                        },
                        "workspace": {"type": "string", "description": "Workspace root path (defaults to cwd)"},
                        "target": {"type": "string", "description": "Test target (file, module, or specific test)"},
                        "framework": {"type": "string", "enum": ["pytest", "jest", "unittest"], "description": "Test framework"},
                        "extra_args": {"type": "array", "items": {"type": "string"}, "description": "Additional CLI args"},
                        "session_id": {"type": "string", "description": "Session ID for iterate/get_session"},
                        "max_iterations": {"type": "integer", "description": "Max diagnostic iterations"},
                        "test_timeout": {"type": "integer", "description": "Test execution timeout in seconds"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "patch_planner",
                "description": "Patch planner — decompose large changes into an ordered, risk-assessed plan. Classifies risk per file (scope, config, ownership, test coverage, dependency fan-out), resolves owners, orders changes topologically by dependency, and groups by reviewer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["plan", "assess_risk", "order_changes"],
                        },
                        "changes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "change_type": {"type": "string", "enum": ["add", "modify", "delete", "rename"]},
                                    "description": {"type": "string"},
                                    "lines_added": {"type": "integer"},
                                    "lines_removed": {"type": "integer"},
                                    "language": {"type": "string"},
                                    "is_test": {"type": "boolean"},
                                    "is_config": {"type": "boolean"},
                                },
                                "required": ["path"],
                            },
                            "description": "List of file changes to plan",
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "heartbeat",
                "description": "Heartbeat, incremental progress and process watchdog. Start/stop periodic heartbeats for long-running tools, track multi-phase progress with ETA, and monitor processes/windows for frozen/unresponsive state with automatic recovery (retry message, graceful close, kill, restart).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["start_heartbeat", "stop_heartbeat", "heartbeat_status", "touch",
                                     "start_progress", "update_progress", "complete_progress", "progress_status",
                                     "add_watch_target", "remove_watch_target", "start_watchdog", "stop_watchdog",
                                     "check_frozen", "watchdog_status", "check_once"],
                        },
                        "tool_name": {"type": "string", "description": "Tool name for heartbeat/progress tracking"},
                        "run_id": {"type": "string", "description": "Run identifier"},
                        "task_id": {"type": "string", "description": "Task identifier"},
                        "interval": {"type": "number", "description": "Heartbeat interval in seconds"},
                        "phases": {"type": "array", "items": {"type": "string"}, "description": "Progress phases"},
                        "phase": {"type": "string", "description": "Current phase name"},
                        "percent": {"type": "number", "description": "Progress percentage (0-100)"},
                        "message": {"type": "string", "description": "Progress message"},
                        "eta_ms": {"type": "integer", "description": "Estimated time remaining in ms"},
                        "pid": {"type": "integer", "description": "Process ID to watch"},
                        "hwnd": {"type": "integer", "description": "Window handle to watch"},
                        "process_name": {"type": "string", "description": "Process name to watch"},
                        "title_pattern": {"type": "string", "description": "Window title pattern to check"},
                        "restart_command": {"type": "string", "description": "Command to restart the process"},
                        "check_interval": {"type": "number", "description": "Watchdog check interval in seconds"},
                        "freeze_threshold_ms": {"type": "integer", "description": "How long frozen before recovery"},
                        "max_recovery_attempts": {"type": "integer", "description": "Max recovery attempts"},
                        "auto_restart": {"type": "boolean", "description": "Auto-restart after kill"},
                        "target_key": {"type": "string", "description": "Watch target key to remove"},
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
                "name": "subagent",
                "description": (
                    "Spawn isolated sub-agents for complex discovery work that can run in parallel, "
                    "then merge their findings. Use when independent branches can investigate network, "
                    "drivers, web research, files, or system state at the same time."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["run_parallel_branches"]},
                        "objective": {"type": "string", "description": "Overall objective the branches support."},
                        "branches": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 6,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "objective": {"type": "string"},
                                    "context": {"type": "string"},
                                    "success_criteria": {"type": "string"},
                                    "budget": {
                                        "type": "object",
                                        "properties": {
                                            "max_steps": {"type": "integer", "minimum": 1, "maximum": 8},
                                            "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
                                            "max_tool_calls": {"type": "integer", "minimum": 0, "maximum": 20},
                                            "max_estimated_tokens": {"type": "integer", "minimum": 250, "maximum": 30000},
                                            "max_cost_usd": {"type": "number", "minimum": 0},
                                            "estimated_usd_per_1k_tokens": {"type": "number", "minimum": 0},
                                            "max_context_chars": {"type": "integer", "minimum": 1000, "maximum": 120000},
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "required": ["objective"],
                                "additionalProperties": False,
                            },
                        },
                        "budget": {
                            "type": "object",
                            "properties": {
                                "max_steps": {"type": "integer", "minimum": 1, "maximum": 8},
                                "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
                                "max_tool_calls": {"type": "integer", "minimum": 0, "maximum": 20},
                                "max_estimated_tokens": {"type": "integer", "minimum": 250, "maximum": 30000},
                                "max_cost_usd": {"type": "number", "minimum": 0},
                                "estimated_usd_per_1k_tokens": {"type": "number", "minimum": 0},
                                "max_context_chars": {"type": "integer", "minimum": 1000, "maximum": 120000},
                            },
                            "additionalProperties": False,
                        },
                        "stop_on_first_success": {
                            "type": "boolean",
                            "description": "Cancel remaining branches when one branch reports the overall objective is satisfied.",
                        },
                    },
                    "required": ["action", "branches"],
                    "additionalProperties": False,
                },
            },
        },
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
        detail = params.get("key") or params.get("entity_type") or params.get("goal_type") or params.get("query") or params.get("strategy_id") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
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
    if tool == "sandbox":
        lang = "Python" if action == "execute_python" else "PowerShell"
        snippet = (params.get("code") or "")[:60]
        return f"Run {lang} script: {snippet}".strip()
    if tool == "artifact":
        detail = params.get("path") or params.get("output_path") or params.get("operation") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    if tool == "dynamic_tool":
        detail = params.get("name") or params.get("tool_id") or ""
        return f"{action.replace('_', ' ').title()} {detail}".strip()
    if tool == "skill":
        detail = params.get("name") or params.get("query") or ""
        return f"Skill {action.replace('_', ' ').title()} {detail}".strip()
    if tool == "visual":
        detail = params.get("title") or params.get("text") or params.get("target_action") or ""
        return f"Visual {action.replace('_', ' ').title()} {detail}".strip()
    if tool == "codebase_map":
        detail = params.get("name") or params.get("module") or params.get("query") or params.get("path") or ""
        return f"Codebase {action.replace('_', ' ').title()} {detail}".strip()
    if tool == "execution_economics":
        detail = params.get("run_id") or params.get("tool") or ""
        return f"Economics {action.replace('_', ' ').title()} {detail}".strip()
    if tool == "test_diagnostic":
        detail = params.get("target") or params.get("session_id") or ""
        return f"Test Diagnostic {action.replace('_', ' ').title()} {detail}".strip()
    if tool == "patch_planner":
        return f"Patch {action.replace('_', ' ').title()}"
    if tool == "heartbeat":
        detail = params.get("tool_name") or params.get("target_key") or ""
        return f"Heartbeat {action.replace('_', ' ').title()} {detail}".strip()
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
        params = {key: value for key, value in arguments.items() if key in {
            "key", "value", "entity_type", "attributes", "query",
            "confidence", "source", "tags", "ttl_seconds", "app_context", "min_confidence", "boost_confidence",
            "strategy_id", "goal_type", "goal_summary", "steps", "error", "duration_ms",
        } and value is not None}
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
                "dest",
                "new_name",
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
        params = {key: value for key, value in arguments.items() if key in {"path", "root", "query", "limit", "filters", "use_ocr"} and value is not None}
    elif tool_name == "office":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"path", "output_path", "app_name", "to", "subject", "body", "content", "title", "attachment_path", "query", "sheet_name", "range_address", "limit", "folder"}
            and value is not None
        }
    elif tool_name == "sandbox":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"code", "timeout", "work_dir", "input_files", "capabilities", "request_id"}
            and value is not None
        }
    elif tool_name == "artifact":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"path", "operation", "params", "content", "output_path"}
            and value is not None
        }
    elif tool_name == "dynamic_tool":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"name", "description", "code", "language", "tool_id", "params", "tags", "tag", "timeout"}
            and value is not None
        }
    elif tool_name == "skill":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {
                "name", "version", "display_name", "description", "author",
                "language", "code", "permissions", "inputs", "outputs",
                "risk", "tags", "entry_point", "dependencies",
                "params", "timeout", "tag", "permission", "max_risk",
                "query", "objective", "limit", "min_score", "trusted_only",
                "review_notes", "tool_id", "tool_spec",
            }
            and value is not None
        }
    elif tool_name == "visual":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {
                "hwnd", "title", "process_name", "region", "text",
                "tool", "target_action", "native_failed", "native_error",
                "grounded", "grounding_score", "semantic_type", "mutates_state",
            }
            and value is not None
        }
    elif tool_name == "codebase_map":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {
                "workspace", "name", "kind", "module", "path",
                "path_pattern", "config_type", "owner", "query", "force",
            }
            and value is not None
        }
    elif tool_name == "execution_economics":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {
                "run_id", "model", "budget_usd", "budget_tokens",
                "step_index", "tool", "tool_action", "status",
                "prompt_tokens", "completion_tokens", "duration_ms",
                "is_retry", "is_replan", "error",
                "current_confidence", "goal_progress", "consecutive_errors",
                "strategies", "route_a", "route_b", "steps", "branch_count",
                "request_id", "replan", "validate_tasks", "min_samples", "limit", "plan_steps",
                "runtime_mode", "provider",
            }
            and value is not None
        }
    elif tool_name == "test_diagnostic":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {
                "workspace", "target", "framework", "extra_args",
                "session_id", "max_iterations", "test_timeout",
            }
            and value is not None
        }
    elif tool_name == "patch_planner":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {"changes"}
            and value is not None
        }
    elif tool_name == "heartbeat":
        action = arguments.get("action")
        params = {
            key: value
            for key, value in arguments.items()
            if key in {
                "tool_name", "run_id", "task_id", "interval", "phases", "phase",
                "percent", "message", "eta_ms", "pid", "hwnd", "process_name",
                "title_pattern", "restart_command", "check_interval",
                "freeze_threshold_ms", "max_recovery_attempts", "auto_restart",
                "target_key",
            }
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
