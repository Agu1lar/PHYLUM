# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
import asyncio
from typing import Dict, Any, List
import logging
from agent_persistence import Persistence
from router import GraphRouter

logger = logging.getLogger(__name__)

class ExecutionEngine:
    def __init__(self, graph, loop: asyncio.AbstractEventLoop = None):
        self.graph = graph
        self.router = GraphRouter(graph)
        self.loop = loop or asyncio.get_event_loop()
        self.persistence = Persistence.get()

    async def run_node(self, node_id: str, state: Dict[str, Any], node_obj) -> Dict[str, Any]:
        attempts = getattr(node_obj.config, 'retries', 2)
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                ok = await node_obj.validate(state)
                if not ok:
                    raise RuntimeError(f"Validation failed for {node_id}")
                result = await asyncio.wait_for(node_obj.execute(state), timeout=node_obj.config.timeout)
                verified = await node_obj.verify(state, result)
                state.setdefault('history', {})[node_id] = {'result': result, 'attempt': attempt}
                await self.persistence.save_kv(f"state:{state.get('request_id')}", state)
                if verified:
                    return result
                else:
                    raise RuntimeError(f"Verification failed for {node_id}")
            except Exception as exc:
                last_exc = exc
                logger.exception("Node %s attempt %s failed: %s", node_id, attempt, exc)
                await asyncio.sleep(min(2 ** attempt, 10))
        raise last_exc

    async def rollback_path(self, executed_nodes: List[str], state: Dict[str, Any]):
        for node_id in reversed(executed_nodes):
            node_obj = getattr(self.graph, 'nodes', {}).get(node_id)
            if not node_obj:
                continue
            try:
                result = state.get('history', {}).get(node_id, {}).get('result', {})
                await node_obj.rollback(state, result)
                logger.info("Rolled back %s", node_id)
            except Exception:
                logger.exception("Rollback failed for %s", node_id)

    async def execute(self, start_node: str, initial_state: Dict[str, Any]):
        state = initial_state
        executed = []
        queue = [start_node]
        while queue:
            node_id = queue.pop(0)
            node_obj = getattr(self.graph, 'nodes', {}).get(node_id)
            if node_obj is None:
                logger.error("Unknown node %s", node_id)
                continue
            state['current_node'] = node_id
            try:
                result = await self.run_node(node_id, state, node_obj)
                executed.append(node_id)
            except Exception as exc:
                logger.exception("Execution failed at %s: %s", node_id, exc)
                await self.rollback_path(executed, state)
                raise
            edges = self.router.next_edges(node_id, state)
            if not edges:
                break
            parallel_edges = [e for e in edges if e.parallel]
            serial_edges = [e for e in edges if not e.parallel]
            if parallel_edges:
                tasks = [self.run_node(e.dst, state, getattr(self.graph, 'nodes', {}).get(e.dst)) for e in parallel_edges]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for idx, res in enumerate(results):
                    e = parallel_edges[idx]
                    if isinstance(res, Exception):
                        logger.exception("Parallel node %s failed: %s", e.dst, res)
                        await self.rollback_path(executed, state)
                        raise res
                    else:
                        executed.append(e.dst)
            for e in serial_edges:
                queue.append(e.dst)
        await self.persistence.save_kv(f"state:{state.get('request_id')}", state)
        return state
