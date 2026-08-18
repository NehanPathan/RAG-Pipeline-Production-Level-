from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.workflow.context import WorkflowContext

#: Sentinel meaning "stop here". A distinct object rather than None, because
#: a node returning None by accident (forgetting a return) would otherwise be
#: indistinguishable from deliberately ending the workflow.
END = "__end__"


@dataclass(frozen=True)
class NodeOutcome:
    """What a node decided.

    `next_node` overrides the statically-configured edge, which is what makes
    branching possible: the router node inspects the query and names the node
    that should run next.
    """

    next_node: str | None = None
    halt: bool = False

    @staticmethod
    def go(node: str) -> NodeOutcome:
        return NodeOutcome(next_node=node)

    @staticmethod
    def stop() -> NodeOutcome:
        return NodeOutcome(halt=True)

    @staticmethod
    def continue_() -> NodeOutcome:
        """Follow the statically-configured edge."""
        return NodeOutcome()


class Node(ABC):
    """One step in a workflow.

    Subclasses implement `run`, mutating the context and optionally naming
    the next node. The engine handles tracing, metrics and error containment,
    so a node contains only its own logic.
    """

    #: Identifier used in edges and traces. Defaults to the class name.
    name: str = ""

    def __init__(self, name: str | None = None) -> None:
        self.name = name or self.name or _snake_case(type(self).__name__)

    @abstractmethod
    async def run(self, context: WorkflowContext) -> NodeOutcome:
        """Do the work. Return where to go next."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"


class FunctionNode(Node):
    """Wraps a plain async function as a node.

    Exists so trivial steps (a lookup, a transformation) do not need a class
    each — the ceremony would discourage decomposition, which is the whole
    point of the engine.
    """

    def __init__(self, name: str, func) -> None:
        super().__init__(name=name)
        self._func = func

    async def run(self, context: WorkflowContext) -> NodeOutcome:
        outcome = await self._func(context)
        return outcome if isinstance(outcome, NodeOutcome) else NodeOutcome.continue_()


def _snake_case(name: str) -> str:
    out: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            out.append("_")
        out.append(char.lower())
    return "".join(out).removesuffix("_node")
