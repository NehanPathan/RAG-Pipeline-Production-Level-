from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.governance.rbac import Principal


@dataclass
class WorkflowContext:
    """State threaded through every node in one run.

    A single mutable object rather than each node returning a new one: nodes
    accumulate partial results (the processed query, then the candidates,
    then the citations) and threading an immutable object would mean every
    node knowing the full shape of the state to copy it forward.

    `trail` records what actually executed. When a query takes an unexpected
    route, the trail is the first thing to look at, and it costs one list
    append per node.
    """

    query: str
    principal: Principal | None = None
    trace_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    trail: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> WorkflowContext:
        """Set a value and return self, so nodes can chain assignments."""
        self.data[key] = value
        return self

    def update(self, **values: Any) -> WorkflowContext:
        self.data.update(values)
        return self

    def require(self, key: str) -> Any:
        """Fetch a value a node depends on, failing loudly if it is absent.

        A missing key means the graph is wired wrong — a node running before
        the one that produces its input. Silently receiving None would push
        that error downstream to somewhere it is much harder to diagnose.
        """
        if key not in self.data:
            raise KeyError(
                f"Workflow context is missing {key!r}. Trail so far: {self.trail}. "
                "A node ran before the one that produces this value."
            )
        return self.data[key]

    def record_error(self, message: str) -> None:
        self.errors.append(message)

    @property
    def failed(self) -> bool:
        return bool(self.errors)
