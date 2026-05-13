# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Router / conditional edge evaluation for the StateGraph.
"""
from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class Edge:
    src: str
    dst: str
    condition: Optional[Callable[[Dict[str, Any]], bool]] = None
    parallel: bool = False
    retries: int = 0
    timeout: Optional[float] = None
    meta: Dict[str, Any] = None


class GraphRouter:
    def __init__(self, graph):
        self.graph = graph

    def next_edges(self, src: str, state: Dict[str, Any]) -> List[Edge]:
        edges = []
        raw = []
        try:
            raw = self.graph.get_adjacent(src)
        except Exception:
            # Fallback if graph stores edges differently
            raw = [e for e in getattr(self.graph, "edges", []) if e.get("src") == src]

        for e in raw:
            cond = e.get("condition")
            ok = True
            if cond is not None:
                try:
                    ok = cond(state)
                except Exception as exc:
                    logger.exception("Condition evaluation failed: %s", exc)
                    ok = False
            if ok:
                edges.append(Edge(src=e["src"], dst=e["dst"], condition=cond, parallel=e.get("parallel", False), retries=e.get("meta", {}).get("retries", 0), timeout=e.get("meta", {}).get("timeout", None), meta=e.get("meta", {})))
        return edges
