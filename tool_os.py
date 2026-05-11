import logging
from pydantic import BaseModel
from tool_base import BaseTool
import platform

logger = logging.getLogger(__name__)


class OSOutput(BaseModel):
    system: str
    release: str
    version: str
    machine: str
    processor: str


class OSIntrospectionTool(BaseTool):
    OutputModel = OSOutput

    async def _run(self, payload: BaseModel = None) -> OSOutput:
        # run blocking calls in thread
        system = platform.system()
        release = platform.release()
        version = platform.version()
        machine = platform.machine()
        processor = platform.processor()
        return OSOutput(system=system, release=release, version=version, machine=machine, processor=processor)
