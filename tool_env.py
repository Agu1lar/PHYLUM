import ctypes
import logging
import os
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from agent_persistence import Persistence
from tool_base import BaseTool

logger = logging.getLogger(__name__)


def _broadcast_env_change() -> None:
    if hasattr(ctypes, "windll"):
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            2000,
            None,
        )


def _get_user_env(name: str) -> Optional[str]:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except Exception:
        return None


def _set_user_env(name: str, value: str) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    _broadcast_env_change()


def _unset_user_env(name: str) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return
    _broadcast_env_change()


class EnvInput(BaseModel):
    action: str = Field(..., pattern='^(get|set|unset|append_path|remove_path|list_path_entries|backup|restore)$')
    name: Optional[str] = None
    value: Optional[str] = None
    scope: str = Field("user")
    entry: Optional[str] = None
    backup_id: Optional[str] = None


class EnvOutput(BaseModel):
    success: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class EnvManagerTool(BaseTool):
    InputModel = EnvInput
    OutputModel = EnvOutput

    def __init__(self, *, default_timeout: int = 15, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.persistence = Persistence.get()

    async def validate(self, payload: EnvInput) -> None:
        if payload.scope not in {"process", "user"}:
            raise ValueError("scope must be process or user")
        if payload.action in {"get", "set", "unset"} and not payload.name:
            raise ValueError("name is required")
        if payload.action == "set" and payload.value is None:
            raise ValueError("value is required for set")
        if payload.action in {"append_path", "remove_path"} and not payload.entry:
            raise ValueError("entry is required")
        if payload.action == "restore" and not payload.backup_id:
            raise ValueError("backup_id is required")

    async def _run(self, payload: EnvInput) -> EnvOutput:
        if payload.action == "backup":
            backup_id = f"env-backup:{payload.scope}:{os.urandom(4).hex()}"
            value = os.environ.get("PATH", "") if payload.scope == "process" else (_get_user_env("Path") or "")
            await self.persistence.save_kv(backup_id, {"scope": payload.scope, "path": value})
            return EnvOutput(success=True, message="backup", details={"backup_id": backup_id, "scope": payload.scope})

        if payload.action == "restore":
            backup = await self.persistence.get_kv(payload.backup_id or "")
            if not backup:
                return EnvOutput(success=False, message="backup not found", details={"backup_id": payload.backup_id})
            if backup["scope"] == "process":
                os.environ["PATH"] = backup.get("path") or ""
            else:
                _set_user_env("Path", backup.get("path") or "")
            return EnvOutput(success=True, message="restore", details=backup)

        if payload.action == "list_path_entries":
            path_value = os.environ.get("PATH", "") if payload.scope == "process" else (_get_user_env("Path") or "")
            entries = [entry for entry in path_value.split(os.pathsep) if entry]
            return EnvOutput(success=True, message="list_path_entries", details={"entries": entries, "scope": payload.scope})

        if payload.action == "append_path":
            current = os.environ.get("PATH", "") if payload.scope == "process" else (_get_user_env("Path") or "")
            entries = [entry for entry in current.split(os.pathsep) if entry]
            if payload.entry not in entries:
                entries.append(payload.entry or "")
            new_value = os.pathsep.join(entries)
            if payload.scope == "process":
                os.environ["PATH"] = new_value
            else:
                _set_user_env("Path", new_value)
            return EnvOutput(success=True, message="append_path", details={"entries": entries, "scope": payload.scope})

        if payload.action == "remove_path":
            current = os.environ.get("PATH", "") if payload.scope == "process" else (_get_user_env("Path") or "")
            entries = [entry for entry in current.split(os.pathsep) if entry and entry != payload.entry]
            new_value = os.pathsep.join(entries)
            if payload.scope == "process":
                os.environ["PATH"] = new_value
            else:
                _set_user_env("Path", new_value)
            return EnvOutput(success=True, message="remove_path", details={"entries": entries, "scope": payload.scope})

        if payload.scope == "process":
            if payload.action == "get":
                return EnvOutput(success=True, message="get", details={"name": payload.name, "value": os.environ.get(payload.name or "")})
            if payload.action == "set":
                os.environ[payload.name or ""] = payload.value or ""
                return EnvOutput(success=True, message="set", details={"name": payload.name, "value": payload.value, "scope": payload.scope})
            if payload.action == "unset":
                os.environ.pop(payload.name or "", None)
                return EnvOutput(success=True, message="unset", details={"name": payload.name, "scope": payload.scope})

        if payload.action == "get":
            return EnvOutput(success=True, message="get", details={"name": payload.name, "value": _get_user_env(payload.name or "")})
        if payload.action == "set":
            _set_user_env(payload.name or "", payload.value or "")
            return EnvOutput(success=True, message="set", details={"name": payload.name, "value": payload.value, "scope": payload.scope})
        if payload.action == "unset":
            _unset_user_env(payload.name or "")
            return EnvOutput(success=True, message="unset", details={"name": payload.name, "scope": payload.scope})

        raise ValueError(f"unsupported env_manager action: {payload.action}")
