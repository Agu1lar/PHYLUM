from __future__ import annotations

import logging
from typing import Dict, Optional

from pydantic import BaseModel, Field

from action_models import ActionEffects, ActionIssue, ActionResult
from share_discovery_agent import ShareDiscoveryAgent
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class ShareDiscoveryInput(BaseModel):
    action: str = Field(..., pattern="^(list_mappings|list_explorer_context|inspect_share|discover_targets)$")
    path: Optional[str] = None
    query: Optional[str] = None
    limit: Optional[int] = None


class ShareDiscoveryTool(BaseTool):
    InputModel = ShareDiscoveryInput
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.agent = ShareDiscoveryAgent()

    async def validate(self, payload: ShareDiscoveryInput) -> None:
        if payload.action == "inspect_share" and not payload.path:
            raise ValueError("inspect_share requires path")

    async def _run(self, payload: ShareDiscoveryInput) -> ActionResult:
        target = {key: value for key, value in {"path": payload.path, "query": payload.query}.items() if value is not None}
        try:
            if payload.action == "list_mappings":
                details = await self.agent.list_mappings()
                summary = f"Encontrei {len(details.get('mappings') or [])} mapeamento(s) de rede."
            elif payload.action == "list_explorer_context":
                details = await self.agent.list_explorer_context()
                summary = f"Capturei {len(details.get('windows') or [])} contexto(s) do Explorer."
            elif payload.action == "inspect_share":
                details = await self.agent.inspect_share(payload.path or "", limit=payload.limit or 25)
                exists = bool(details.get("exists"))
                summary = f"Inspecionei {payload.path}." if exists else f"O caminho {payload.path} nao esta acessivel no momento."
            elif payload.action == "discover_targets":
                details = await self.agent.discover_targets(payload.query)
                summary = f"Descobri {len(details.get('candidates') or [])} candidato(s) de share ou drive."
            else:
                raise ValueError(f"unsupported share_discovery action: {payload.action}")
            return ActionResult(
                status="succeeded",
                summary=summary,
                tool="share_discovery",
                action=payload.action,
                semantic_type="inspection",
                target=target,
                data=details,
                effects=ActionEffects(changed=False),
                diagnostics={"source": "powershell"},
            )
        except Exception as exc:
            logger.exception("share discovery failed")
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="share_discovery",
                action=payload.action,
                semantic_type="inspection",
                target=target,
                data={},
                effects=ActionEffects(changed=False),
                issue=ActionIssue(kind="inaccessible_share", message=str(exc), retryable=False),
                diagnostics={"source": "powershell"},
            )

