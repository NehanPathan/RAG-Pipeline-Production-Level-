"""Workflow engine — pipelines as node graphs instead of nested conditionals.

The problem: `QueryPipeline.answer()` had grown into a single method holding
the kill-switch check, the cache lookup, retrieval, context processing, two
policy checks and the streaming loop, with the control flow expressed as
early returns threaded through an async generator. Every new capability made
that method longer and its branches harder to reason about, and none of it
was individually testable or individually observable.

A workflow states the same logic as nodes and edges:

    workflow = Workflow("answer")
    workflow.add(CheckKillSwitchNode(), next_="cache")
    workflow.add(CacheLookupNode(), name="cache", next_=decide_after_cache)
    ...
    result = await workflow.run(context)

What this buys, concretely:

  * Each node is a class with one job, unit-testable in isolation.
  * Every node execution is traced and counted, so "which step is slow" and
    "which step fails" are answerable without adding instrumentation.
  * A node can decide the next node dynamically, which is what the query
    router needs and what an if/elif chain cannot express cleanly.
  * Adding a capability is adding a node, not editing a method everything
    else depends on.
"""

from src.workflow.context import WorkflowContext
from src.workflow.engine import Workflow, WorkflowError, WorkflowResult
from src.workflow.node import END, Node, NodeOutcome

__all__ = [
    "END",
    "Node",
    "NodeOutcome",
    "Workflow",
    "WorkflowContext",
    "WorkflowError",
    "WorkflowResult",
]
