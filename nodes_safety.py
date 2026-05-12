from nodes_base import BaseNode
from typing import Dict, Any
import logging
from risk_classifier import classify

logger = logging.getLogger(__name__)

class SafetyNode(BaseNode):
    async def validate(self, state: Dict[str, Any]) -> bool:
        return bool(state.get("current_task"))

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task = state["current_task"]
        tool = task.get("tool")
        action = task.get("action")
        params = task.get("params", {})
        verdict = {
            "status": "allow",
            "reason": "policy passed",
            "requires_approval": False,
            "risk": {"level": "low", "tags": [], "reason": "default allow"},
        }
        if tool not in {"shell", "filesystem", "memory", "browser", "web", "package_manager", "software_inventory", "env_manager", "driver_manager", "os", "desktop"}:
            verdict["status"] = "deny"
            verdict["reason"] = f"unsupported tool: {tool}"
            verdict["risk"] = {"level": "high", "tags": ["unsupported"], "reason": "unsupported tool"}
            return {"safety": verdict}

        if tool == "shell":
            command = params.get("command", "")
            risk = classify(command)
            verdict["risk"] = risk
            if not command:
                verdict["status"] = "deny"
                verdict["reason"] = "shell command missing"
            elif risk["level"] == "high":
                verdict["status"] = "require_approval"
                verdict["reason"] = "shell command classified as high risk"
                verdict["requires_approval"] = True
            return {"safety": verdict}

        if tool == "filesystem":
            sensitive_actions = {"write", "delete", "move", "mkdir", "copy", "organize_directory", "organize_downloads", "organize_desktop", "clean_temp", "create_structure", "undo"}
            if action in sensitive_actions:
                verdict["status"] = "require_approval"
                verdict["reason"] = f"filesystem action '{action}' requires approval"
                verdict["requires_approval"] = True
                verdict["risk"] = {"level": "medium", "tags": ["filesystem"], "reason": "mutating filesystem"}
            return {"safety": verdict}

        if tool == "memory" and action in {"delete"}:
            verdict["status"] = "require_approval"
            verdict["reason"] = "memory delete requires approval"
            verdict["requires_approval"] = True
            verdict["risk"] = {"level": "medium", "tags": ["memory"], "reason": "destructive memory change"}
            return {"safety": verdict}

        if tool == "browser":
            if action in {"download", "interact_dom", "upload_file"}:
                verdict["status"] = "require_approval"
                verdict["reason"] = f"browser action '{action}' requires approval"
                verdict["requires_approval"] = True
                verdict["risk"] = {"level": "medium", "tags": ["browser"], "reason": "browser action may change state or download files"}
            else:
                verdict["risk"] = {"level": "low", "tags": ["browser"], "reason": "read-only browser action"}
            return {"safety": verdict}

        if tool == "web":
            if action == "download_verified":
                verdict["status"] = "require_approval"
                verdict["reason"] = "web download_verified requires approval"
                verdict["requires_approval"] = True
                verdict["risk"] = {"level": "medium", "tags": ["web", "download"], "reason": "download from the internet"}
            else:
                verdict["risk"] = {"level": "low", "tags": ["web"], "reason": "read-only web research"}
            return {"safety": verdict}

        if tool == "package_manager":
            if action in {"install", "uninstall", "upgrade"}:
                verdict["status"] = "require_approval"
                verdict["reason"] = f"package action '{action}' requires approval"
                verdict["requires_approval"] = True
                verdict["risk"] = {"level": "high", "tags": ["package_manager"], "reason": "system package mutation"}
            else:
                verdict["risk"] = {"level": "low", "tags": ["package_manager"], "reason": "package inspection"}
            return {"safety": verdict}

        if tool == "software_inventory":
            verdict["risk"] = {"level": "low", "tags": ["software_inventory"], "reason": "software inspection"}
            return {"safety": verdict}

        if tool == "env_manager":
            if action in {"set", "unset", "append_path", "remove_path", "restore"}:
                verdict["status"] = "require_approval"
                verdict["reason"] = f"env_manager action '{action}' requires approval"
                verdict["requires_approval"] = True
                verdict["risk"] = {"level": "high", "tags": ["env_manager"], "reason": "environment variables affect the system"}
            else:
                verdict["risk"] = {"level": "low", "tags": ["env_manager"], "reason": "environment inspection"}
            return {"safety": verdict}

        if tool == "driver_manager":
            if action in {"install_inf", "add_driver_package", "rollback_driver", "scan_hardware_changes", "restart_spooler"}:
                verdict["status"] = "require_approval"
                verdict["reason"] = f"driver_manager action '{action}' requires approval"
                verdict["requires_approval"] = True
                verdict["risk"] = {"level": "high", "tags": ["driver_manager"], "reason": "device/driver mutation can impact the system"}
            else:
                verdict["risk"] = {"level": "medium", "tags": ["driver_manager"], "reason": "device inspection"}
            return {"safety": verdict}

        if tool == "os":
            verdict["risk"] = {"level": "low", "tags": ["os"], "reason": "system inspection"}
            return {"safety": verdict}

        if tool == "desktop":
            if action == "service_action":
                verdict["status"] = "require_approval"
                verdict["reason"] = "desktop service_action requires approval"
                verdict["requires_approval"] = True
                verdict["risk"] = {"level": "high", "tags": ["desktop", "service"], "reason": "service control can impact the system"}
            elif action in {"focus_window", "clipboard_set", "notify"}:
                verdict["risk"] = {"level": "medium", "tags": ["desktop"], "reason": "desktop mutation without shell"}
            else:
                verdict["risk"] = {"level": "low", "tags": ["desktop"], "reason": "desktop inspection"}
        return {"safety": verdict}

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return result.get("safety", {}).get("status") != "deny"
