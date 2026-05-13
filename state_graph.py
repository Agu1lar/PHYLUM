"""State Graph Engine: replaces linear pipeline execution with a directed graph.

Nodes represent execution phases (plan, safety, execute, reflect, recover, checkpoint).
Edges define conditional transitions between nodes based on the run state.
The graph executor walks the graph, tracking the current node at all times so the
RecoveryEngine can return execution to any specific node on failure.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class NodeType(str, Enum):
    ENTRY = "entry"
    PLANNER = "planner"
    ROUTER = "router"
    SAFETY = "safety"
    APPROVAL = "approval"
    EXECUTOR = "executor"
    REFLECTION = "reflection"
    RECOVERY = "recovery"
    CHECKPOINT = "checkpoint"
    HANDOFF = "handoff"
    COMPLETE = "complete"
    FAIL = "fail"
    SCRIPT_RECOVERY = "script_recovery"


NodeHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
EdgeCondition = Callable[[Dict[str, Any], Dict[str, Any]], bool]


@dataclass
class GraphNode:
    """A node in the state graph."""
    node_id: str
    node_type: NodeType
    handler: Optional[NodeHandler] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash(self.node_id)

    def __eq__(self, other):
        if isinstance(other, GraphNode):
            return self.node_id == other.node_id
        return NotImplemented


@dataclass
class GraphEdge:
    """A directed edge between two graph nodes with an optional condition."""
    source: str
    target: str
    condition: Optional[EdgeCondition] = None
    priority: int = 50
    label: str = ""

    def matches(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        if self.condition is None:
            return True
        try:
            return self.condition(state, result)
        except Exception:
            return False


class StateGraph:
    """A directed graph of execution nodes with conditional edges.

    Build with add_node/add_edge, then compile() to validate.
    Execute via GraphExecutor.
    """
    def __init__(self, name: str = "default"):
        self.name = name
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: Dict[str, List[GraphEdge]] = {}
        self.entry_node: Optional[str] = None
        self._compiled = False

    def add_node(
        self,
        node_id: str,
        node_type: NodeType,
        handler: Optional[NodeHandler] = None,
        **metadata,
    ) -> StateGraph:
        node = GraphNode(node_id=node_id, node_type=node_type, handler=handler, metadata=metadata)
        self.nodes[node_id] = node
        self.edges.setdefault(node_id, [])
        if node_type == NodeType.ENTRY:
            self.entry_node = node_id
        self._compiled = False
        return self

    def add_edge(
        self,
        source: str,
        target: str,
        condition: Optional[EdgeCondition] = None,
        priority: int = 50,
        label: str = "",
    ) -> StateGraph:
        edge = GraphEdge(source=source, target=target, condition=condition, priority=priority, label=label)
        self.edges.setdefault(source, []).append(edge)
        self.edges[source].sort(key=lambda e: e.priority)
        self._compiled = False
        return self

    def compile(self) -> StateGraph:
        if not self.entry_node:
            entry_nodes = [nid for nid, n in self.nodes.items() if n.node_type == NodeType.ENTRY]
            if not entry_nodes:
                raise ValueError("Graph has no ENTRY node")
            self.entry_node = entry_nodes[0]

        for node_id in self.nodes:
            if node_id not in self.edges:
                self.edges[node_id] = []

        for source, edge_list in self.edges.items():
            if source not in self.nodes:
                raise ValueError(f"Edge source '{source}' is not a graph node")
            for edge in edge_list:
                if edge.target not in self.nodes:
                    raise ValueError(f"Edge target '{edge.target}' is not a graph node")

        terminal_types = {NodeType.COMPLETE, NodeType.FAIL, NodeType.HANDOFF}
        for node_id, node in self.nodes.items():
            if node.node_type not in terminal_types and not self.edges.get(node_id):
                logger.warning("Node '%s' has no outgoing edges and is not terminal", node_id)

        self._compiled = True
        return self

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def successors(self, node_id: str) -> List[str]:
        return [edge.target for edge in self.edges.get(node_id, [])]

    def predecessors(self, node_id: str) -> List[str]:
        result = []
        for source, edge_list in self.edges.items():
            for edge in edge_list:
                if edge.target == node_id and source not in result:
                    result.append(source)
        return result

    def find_nodes_by_type(self, node_type: NodeType) -> List[GraphNode]:
        return [n for n in self.nodes.values() if n.node_type == node_type]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entry": self.entry_node,
            "nodes": {
                nid: {"type": n.node_type.value, "metadata": n.metadata}
                for nid, n in self.nodes.items()
            },
            "edges": {
                source: [
                    {"target": e.target, "priority": e.priority, "label": e.label}
                    for e in edges
                ]
                for source, edges in self.edges.items()
            },
        }


class GraphTraversalLog:
    """Records every node visited during a graph execution."""
    def __init__(self):
        self.entries: List[Dict[str, Any]] = []

    def record(self, node_id: str, node_type: str, result_keys: Optional[List[str]] = None, error: Optional[str] = None):
        self.entries.append({
            "node_id": node_id,
            "node_type": node_type,
            "result_keys": result_keys,
            "error": error,
        })

    @property
    def visited_nodes(self) -> List[str]:
        return [e["node_id"] for e in self.entries]

    @property
    def last_node(self) -> Optional[str]:
        return self.entries[-1]["node_id"] if self.entries else None

    def find_last_of_type(self, node_type: str) -> Optional[str]:
        for entry in reversed(self.entries):
            if entry["node_type"] == node_type:
                return entry["node_id"]
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"entries": self.entries, "visited": self.visited_nodes}

    def to_serializable(self) -> Dict[str, Any]:
        """Return a plain dict for JSON serialization."""
        return self.to_dict()


class GraphExecutor:
    """Executes a compiled StateGraph by walking nodes and evaluating edge conditions."""

    def __init__(self, graph: StateGraph, *, max_visits: int = 200):
        if not graph._compiled:
            graph.compile()
        self.graph = graph
        self.max_visits = max_visits

    async def run(
        self,
        state: Dict[str, Any],
        *,
        cancel_event: Optional[asyncio.Event] = None,
        start_node: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = start_node or self.graph.entry_node
        if not current:
            raise RuntimeError("No entry node in graph")

        traversal = GraphTraversalLog()
        state["_graph"] = {
            "current_node": current,
            "traversal": traversal,
            "visits": 0,
        }
        visits = 0

        while current:
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError()

            visits += 1
            if visits > self.max_visits:
                raise RuntimeError(f"Graph exceeded max_visits={self.max_visits} (possible infinite loop)")

            node = self.graph.get_node(current)
            if node is None:
                raise RuntimeError(f"Node '{current}' not found in graph")

            state["_graph"]["current_node"] = current
            state["_graph"]["visits"] = visits

            result: Dict[str, Any] = {}
            error: Optional[str] = None
            try:
                if node.handler:
                    result = await node.handler(state) or {}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = str(exc)
                result = {"_error": error, "_exception_type": type(exc).__name__}

            traversal.record(
                node_id=current,
                node_type=node.node_type.value,
                result_keys=list(result.keys()) if result else None,
                error=error,
            )

            state["_graph_result"] = result

            if node.node_type in (NodeType.COMPLETE, NodeType.FAIL):
                state["_graph"]["terminal_node"] = current
                state["_graph"]["terminal_type"] = node.node_type.value
                return state

            if node.node_type == NodeType.HANDOFF:
                state["_graph"]["paused_at"] = current
                return state

            next_node = self._resolve_next(current, state, result)
            if next_node is None:
                if node.node_type in (NodeType.COMPLETE, NodeType.FAIL, NodeType.HANDOFF):
                    return state
                raise RuntimeError(f"No matching edge from node '{current}' (type={node.node_type.value})")

            current = next_node

        return state

    def _resolve_next(self, current: str, state: Dict[str, Any], result: Dict[str, Any]) -> Optional[str]:
        edges = self.graph.edges.get(current, [])
        for edge in edges:
            if edge.matches(state, result):
                return edge.target
        return None

    def find_recovery_target(self, state: Dict[str, Any], recovery_classification: Dict[str, Any]) -> Optional[str]:
        """Given a recovery classification, determine which graph node to jump to."""
        suggested = recovery_classification.get("suggested_action", "")
        target_node = recovery_classification.get("target_node")
        if target_node and target_node in self.graph.nodes:
            return target_node

        action_to_node_type = {
            "retry": NodeType.EXECUTOR,
            "execute_script": NodeType.SCRIPT_RECOVERY,
            "rediscover_target": NodeType.PLANNER,
            "replan": NodeType.PLANNER,
            "switch_tooling": NodeType.PLANNER,
            "verify_outcome": NodeType.REFLECTION,
            "ask_user": NodeType.HANDOFF,
            "narrow_selector": NodeType.EXECUTOR,
        }

        target_type = action_to_node_type.get(suggested)
        if target_type:
            candidates = self.graph.find_nodes_by_type(target_type)
            if candidates:
                return candidates[0].node_id

        traversal = state.get("_graph", {}).get("traversal")
        if traversal and isinstance(traversal, GraphTraversalLog):
            classification = recovery_classification.get("classification", "")
            if classification in ("retryable", "script_recovery"):
                last_exec = traversal.find_last_of_type(NodeType.EXECUTOR.value)
                if last_exec:
                    return last_exec
            if classification == "replan_required":
                last_plan = traversal.find_last_of_type(NodeType.PLANNER.value)
                if last_plan:
                    return last_plan

        entries = state.get("_graph", {}).get("traversal_entries")
        if entries and isinstance(entries, list):
            classification = recovery_classification.get("classification", "")
            if classification in ("retryable", "script_recovery"):
                for entry in reversed(entries):
                    if entry.get("node_type") == NodeType.EXECUTOR.value:
                        return entry["node_id"]
            if classification == "replan_required":
                for entry in reversed(entries):
                    if entry.get("node_type") == NodeType.PLANNER.value:
                        return entry["node_id"]

        return None
