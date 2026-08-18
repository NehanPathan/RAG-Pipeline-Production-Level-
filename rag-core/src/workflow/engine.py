from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import workflow_nodes
from src.monitoring.stage_tracer import traced_stage
from src.workflow.context import WorkflowContext
from src.workflow.node import END, FunctionNode, Node, NodeOutcome

logger = get_logger(__name__)

# An edge is either a fixed node name or a function that picks one from the
# context. The callable form is what lets a workflow branch on a value
# computed at runtime without the deciding node needing to know the graph.
Edge = str | Callable[[WorkflowContext], str] | None


class WorkflowError(RuntimeError):
    pass


@dataclass
class WorkflowResult:
    context: WorkflowContext
    completed: bool
    steps: int
    halted_at: str | None = None

    @property
    def trail(self) -> list[str]:
        return self.context.trail


@dataclass
class Workflow:
    """A named node graph, executed one node at a time.

    Deliberately sequential and single-threaded. A workflow here expresses
    *decision flow*, not parallelism — the places that genuinely need
    concurrency (vector and BM25 search running together) already use
    `asyncio.gather` inside a single node, which keeps the graph readable
    and leaves the concurrency where it is actually understood.
    """

    name: str
    #: Hard stop on cycles. A misconfigured edge that points backwards would
    #: otherwise loop until the request times out, which reads as a hang
    #: rather than the configuration error it is.
    max_steps: int = 50
    _nodes: dict[str, Node] = field(default_factory=dict, repr=False)
    _edges: dict[str, Edge] = field(default_factory=dict, repr=False)
    _entry: str | None = field(default=None, repr=False)

    def add(
        self,
        node: Node | Callable,
        *,
        name: str | None = None,
        next_: Edge = None,
        entry: bool = False,
    ) -> Workflow:
        """Register a node. Returns self so definitions read as a chain."""
        if not isinstance(node, Node):
            if name is None:
                raise ValueError("A function node needs an explicit name.")
            node = FunctionNode(name, node)

        node_name = name or node.name
        if node_name in self._nodes:
            raise ValueError(f"Workflow {self.name!r} already has a node named {node_name!r}.")

        self._nodes[node_name] = node
        self._edges[node_name] = next_
        if entry or self._entry is None:
            self._entry = node_name
        return self

    def entry_point(self, name: str) -> Workflow:
        if name not in self._nodes:
            raise ValueError(f"Unknown node {name!r}. Known: {sorted(self._nodes)}")
        self._entry = name
        return self

    @property
    def nodes(self) -> list[str]:
        return list(self._nodes)

    async def run(self, context: WorkflowContext) -> WorkflowResult:
        if not self._entry:
            raise WorkflowError(f"Workflow {self.name!r} has no nodes.")

        current: str | None = self._entry
        steps = 0

        async with traced_stage(f"workflow:{self.name}", entry=self._entry) as stage:
            while current and current != END:
                if steps >= self.max_steps:
                    raise WorkflowError(
                        f"Workflow {self.name!r} exceeded {self.max_steps} steps — "
                        f"the graph probably contains a cycle. Trail: {context.trail}"
                    )

                node = self._nodes.get(current)
                if node is None:
                    raise WorkflowError(
                        f"Workflow {self.name!r} has no node named {current!r}. "
                        f"Known: {sorted(self._nodes)}"
                    )

                context.trail.append(current)
                steps += 1
                outcome = await self._execute(node, context)

                if outcome.halt:
                    stage.set_result(steps=steps, halted_at=current, completed=False)
                    logger.info(
                        "workflow_halted", workflow=self.name, node=current, steps=steps
                    )
                    return WorkflowResult(
                        context=context, completed=False, steps=steps, halted_at=current
                    )

                current = outcome.next_node or self._resolve_edge(current, context)

            stage.set_result(steps=steps, completed=True)
            return WorkflowResult(context=context, completed=True, steps=steps)

    async def _execute(self, node: Node, context: WorkflowContext) -> NodeOutcome:
        try:
            outcome = await node.run(context)
        except Exception as exc:
            workflow_nodes.labels(workflow=self.name, node=node.name, outcome="error").inc()
            logger.error(
                "workflow_node_failed",
                workflow=self.name,
                node=node.name,
                error=str(exc),
                trail=context.trail,
            )
            # Re-raised rather than swallowed: a node that fails has left the
            # context half-built, and continuing would produce a result
            # assembled from partial state — worse than a clean failure the
            # caller can handle.
            raise

        workflow_nodes.labels(workflow=self.name, node=node.name, outcome="ok").inc()
        return outcome or NodeOutcome.continue_()

    def _resolve_edge(self, node_name: str, context: WorkflowContext) -> str | None:
        edge = self._edges.get(node_name)
        if edge is None:
            return None
        if callable(edge):
            return edge(context)
        return edge

    def describe(self) -> dict:
        return {
            "name": self.name,
            "entry": self._entry,
            "nodes": [
                {
                    "name": name,
                    "type": type(node).__name__,
                    "next": self._edges[name] if isinstance(self._edges[name], str) else "dynamic",
                }
                for name, node in self._nodes.items()
            ],
        }
