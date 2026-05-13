from __future__ import annotations

from typing import Any, Dict, List

from canonical_tools import action_metadata, supported_tools
from risk_classifier import classify, normalize_command
from tool_filesystem import is_allowed_path_for_action

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
RUN_SCOPE_FAMILIES = {
    "interactive_desktop": {"desktop", "windows_ui", "browser", "office"},
    "workspace_access": {"filesystem", "share_discovery", "document_intelligence"},
}
INTERACTIVE_DESKTOP_BLOCKLIST = {
    ("desktop", "close_window"),
    ("desktop", "kill_process"),
    ("desktop", "clipboard_set"),
    ("desktop", "notify"),
    ("desktop", "service_action"),
}


class PolicyEngine:
    def grant_family(self, task: Dict[str, Any]) -> str:
        tool = task.get("tool")
        if tool in RUN_SCOPE_FAMILIES["interactive_desktop"]:
            return "interactive_desktop"
        if tool in RUN_SCOPE_FAMILIES["workspace_access"]:
            return "workspace_access"
        return f"tool:{tool}"

    def supports_run_scope(self, task: Dict[str, Any], *, risk: Dict[str, Any] | None = None, metadata: Dict[str, Any] | None = None) -> bool:
        tool = task.get("tool")
        params = task.get("params") or {}
        metadata = metadata or action_metadata(tool, task.get("action"))
        risk = risk or self._risk_for(tool, task.get("action"), params, metadata)
        if metadata.get("double_confirm"):
            return False
        if risk.get("level") == "high":
            return False
        if tool == "shell" and (risk.get("level") != "low" or bool(params.get("require_admin"))):
            return False
        if tool == "filesystem":
            path = params.get("path")
            dest = params.get("dest")
            if (path and not is_allowed_path_for_action(path, task.get("action"))) or (dest and not is_allowed_path_for_action(dest, task.get("action"))):
                return False
        return metadata.get("approval_mode", "none") != "double"

    def evaluate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task = state["current_task"]
        tool = task.get("tool")
        action = task.get("action")
        params = task.get("params") or {}
        runtime_mode = state.get("runtime_mode")

        verdict: Dict[str, Any] = {
            "status": "allow",
            "reason": "policy passed",
            "requires_approval": False,
            "risk": {"level": "low", "tags": [], "reason": "default allow"},
            "approval": None,
            "metadata": action_metadata(tool, action),
        }

        if tool not in supported_tools():
            verdict["status"] = "deny"
            verdict["reason"] = f"unsupported tool: {tool}"
            verdict["risk"] = {"level": "high", "tags": ["unsupported"], "reason": "unsupported tool"}
            return verdict

        metadata = verdict["metadata"]
        predicted_effects = self._predicted_effects(tool, action, params, metadata)
        risk = self._risk_for(tool, action, params, metadata)
        verdict["risk"] = risk

        approval_mode = metadata.get("approval_mode", "none")
        requires_approval = approval_mode != "none"

        if tool == "filesystem":
            outside_sandbox = []
            path = params.get("path")
            dest = params.get("dest")
            if path and not is_allowed_path_for_action(path, action):
                outside_sandbox.append(path)
            if dest and not is_allowed_path_for_action(dest, action):
                outside_sandbox.append(dest)
            if outside_sandbox:
                requires_approval = True
                approval_mode = "double" if metadata.get("double_confirm") else "single"
                predicted_effects.extend(
                    {
                        "entity_type": "path",
                        "operation": "access_outside_sandbox",
                        "path": item,
                    }
                    for item in outside_sandbox
                )
                risk = {"level": "high", "tags": ["filesystem", "sandbox"], "reason": "target outside sandbox"}
                verdict["risk"] = risk

        if runtime_mode != "agentic" and not metadata.get("mutates_state") and tool != "shell":
            requires_approval = False

        if tool == "shell":
            command = params.get("command", "")
            risk = classify(command)
            verdict["risk"] = risk
            if not command:
                verdict["status"] = "deny"
                verdict["reason"] = "shell command missing"
                return verdict
            requires_approval = risk["level"] in {"medium", "high"} or bool(params.get("require_admin"))
            if runtime_mode not in {"agentic", "heuristic"}:
                requires_approval = risk["level"] != "low" or bool(params.get("require_admin"))
            if risk["level"] == "high":
                approval_mode = "double"
            elif params.get("require_admin"):
                approval_mode = "single"
            predicted_effects = [
                {
                    "entity_type": "command",
                    "operation": "run_command",
                    "command": normalize_command(command),
                    "risk_level": risk["level"],
                    "requires_admin": bool(params.get("require_admin")),
                }
            ]

        matching_grant = self._matching_grant(
            state.get("approval_grants") or [],
            task=task,
            metadata=metadata,
            risk=risk,
            params=params,
        )
        if matching_grant is not None and requires_approval:
            requires_approval = False
            verdict["reason"] = "covered by active flow approval"
            verdict["grant"] = {
                "grant_id": matching_grant.get("grant_id"),
                "scope": matching_grant.get("scope"),
                "family": matching_grant.get("family"),
            }

        if requires_approval:
            verdict["status"] = "require_approval"
            verdict["requires_approval"] = True
            verdict["reason"] = self._approval_reason(tool, action, params, predicted_effects)
            verdict["approval"] = {
                "mode": approval_mode,
                "predicted_effects": predicted_effects,
                "reversibility": metadata.get("reversibility"),
                "reason_code": f"{tool}.{action}",
                "metadata": metadata,
            }

        return verdict

    def _risk_for(self, tool: str, action: str, params: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
        if tool == "shell":
            return classify(params.get("command", ""))
        if tool == "desktop" and action == "service_action":
            return {"level": "high", "tags": [tool, "service"], "reason": "service control can impact the system"}
        if metadata.get("double_confirm"):
            return {"level": "high", "tags": [tool, "destructive"], "reason": "double confirmation required"}
        if metadata.get("mutates_state"):
            return {"level": "medium", "tags": [tool], "reason": "state-changing action"}
        return {"level": "low", "tags": [tool], "reason": "inspection"}

    def _predicted_effects(self, tool: str, action: str, params: Dict[str, Any], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        effect_kind = metadata.get("effect_kind") or action
        targets = []
        for field in metadata.get("target_fields") or []:
            value = params.get(field)
            if value is not None:
                targets.append({"field": field, "value": value})
        effect = {
            "entity_type": tool,
            "operation": effect_kind,
        }
        if targets:
            effect["targets"] = targets
        return [effect]

    def _approval_reason(self, tool: str, action: str, params: Dict[str, Any], predicted_effects: List[Dict[str, Any]]) -> str:
        target_bits = []
        for effect in predicted_effects:
            for target in effect.get("targets", []):
                target_bits.append(str(target.get("value")))
        targets = ", ".join(target_bits)
        if targets:
            return f"A acao {tool}.{action} vai atuar em: {targets}"
        return f"A acao {tool}.{action} requer sua aprovacao antes da execucao"

    def _matching_grant(
        self,
        grants: List[Dict[str, Any]],
        *,
        task: Dict[str, Any],
        metadata: Dict[str, Any],
        risk: Dict[str, Any],
        params: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        for grant in grants:
            if self._grant_allows(grant, task=task, metadata=metadata, risk=risk, params=params):
                return grant
        return None

    def _grant_allows(
        self,
        grant: Dict[str, Any],
        *,
        task: Dict[str, Any],
        metadata: Dict[str, Any],
        risk: Dict[str, Any],
        params: Dict[str, Any],
    ) -> bool:
        if grant.get("status") != "active":
            return False
        if grant.get("scope") != "run_scope":
            return False
        if metadata.get("double_confirm"):
            return False
        if RISK_ORDER.get(risk.get("level", "high"), 99) > RISK_ORDER.get(grant.get("max_risk_level", "medium"), 1):
            return False
        if params.get("require_admin"):
            return False
        tool = task.get("tool")
        action = task.get("action")
        family = grant.get("family")
        if family == "interactive_desktop":
            if tool not in RUN_SCOPE_FAMILIES["interactive_desktop"]:
                return False
            if (tool, action) in INTERACTIVE_DESKTOP_BLOCKLIST:
                return False
            return True
        if family == "workspace_access":
            if tool not in RUN_SCOPE_FAMILIES["workspace_access"]:
                return False
            return not metadata.get("mutates_state", False)
        return grant.get("tool") == tool
