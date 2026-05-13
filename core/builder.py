# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Builder for StateGraph and helpers.
Provides a thin wrapper around LangGraph's StateGraph if available, otherwise uses a local fallback.
"""
from typing import Any, Callable, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

try:
    from langgraph import StateGraph  # type: ignore
    logger.info("Using installed langgraph.StateGraph")
except Exception:
    # Minimal fallback StateGraph implementation
    class StateGraph:
        def __init__(self):
            self.nodes = {}
            self.edges = []

        def add_node(self, node_id: str, node_obj: Any):
            self.nodes[node_id] = node_obj

        def add_edge(self, src: str, dst: str, condition: Optional[Callable[[Dict], bool]] = None, parallel: bool = False, meta: Optional[Dict] = None):
            self.edges.append({"src": src, "dst": dst, "condition": condition, "parallel": parallel, "meta": meta or {}})

        def get_adjacent(self, src: str):
            return [e for e in self.edges if e["src"] == src]


class GraphBuilder:
    def __init__(self):
        self.graph = StateGraph()

    def add_node(self, node_id: str, node_obj: Any):
        self.graph.add_node(node_id, node_obj)
        return self

    def add_edge(self, src: str, dst: str, condition: Optional[Callable[[Dict], bool]] = None, parallel: bool = False, meta: Optional[Dict] = None):
        self.graph.add_edge(src, dst, condition, parallel, meta)
        return self

    def build(self) -> StateGraph:
        return self.graph
