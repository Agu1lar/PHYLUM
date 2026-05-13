from __future__ import annotations
import asyncio
import inspect
import logging
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ToolExecutionError(Exception):
    pass


class BaseTool:
    """Base class for tools. Subclasses must implement _run.
    Provides retries, timeout, validation scaffolding and structured response handling.
    """

    class InputModel(BaseModel):
        pass

    class OutputModel(BaseModel):
        pass

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 2):
        self.default_timeout = default_timeout
        self.default_retries = default_retries
        self.logger = logging.getLogger(self.__class__.__name__)

    async def validate(self, payload: BaseModel) -> None:
        """Override for custom validation. Should raise ValueError on invalid input."""
        return None

    async def _run(self, payload: BaseModel) -> BaseModel:
        raise NotImplementedError()

    async def run(
        self,
        payload: Dict[str, Any],
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> BaseModel:
        timeout = timeout or self.default_timeout
        retries = retries if retries is not None else self.default_retries
        try:
            input_model: BaseModel = self.InputModel(**payload)
            await self.validate(input_model)
        except Exception as exc:
            raise ToolExecutionError(f"validation failed: {exc}") from exc

        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                self.logger.info("Tool %s attempt %s/%s", self.__class__.__name__, attempt, retries)
                # enforce per-attempt timeout
                if "cancel_event" in inspect.signature(self._run).parameters:
                    run_coro = self._run(input_model, cancel_event=cancel_event)
                else:
                    run_coro = self._run(input_model)
                result = await asyncio.wait_for(run_coro, timeout=timeout)
                # cast/validate result with OutputModel
                if hasattr(self, 'OutputModel') and self.OutputModel is not None:
                    out = self.OutputModel(**(result.dict() if isinstance(result, BaseModel) else result))
                    return out
                return result
            except asyncio.TimeoutError as te:
                self.logger.warning("Tool %s attempt %s timed out after %s seconds", self.__class__.__name__, attempt, timeout)
                last_exc = te
                await asyncio.sleep(min(2 ** attempt, 10))
            except Exception as exc:
                self.logger.exception("Tool %s attempt %s failed: %s", self.__class__.__name__, attempt, exc)
                last_exc = exc
                await asyncio.sleep(min(2 ** attempt, 10))
        root_message = str(last_exc).strip() if last_exc is not None else "unknown tool error"
        raise ToolExecutionError(f"All {retries} attempts failed: {root_message}") from last_exc
