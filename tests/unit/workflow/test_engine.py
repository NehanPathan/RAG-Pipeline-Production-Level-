from __future__ import annotations

import pytest
from src.workflow import END, Node, NodeOutcome, Workflow, WorkflowContext, WorkflowError


class RecordNode(Node):
    def __init__(self, name: str, next_node: str | None = None, halt: bool = False) -> None:
        super().__init__(name=name)
        self._next = next_node
        self._halt = halt

    async def run(self, context: WorkflowContext) -> NodeOutcome:
        context.set(self.name, True)
        if self._halt:
            return NodeOutcome.stop()
        if self._next:
            return NodeOutcome.go(self._next)
        return NodeOutcome.continue_()


class ExplodingNode(Node):
    name = "boom"

    async def run(self, context: WorkflowContext) -> NodeOutcome:
        raise RuntimeError("node failed")


@pytest.fixture
def context() -> WorkflowContext:
    return WorkflowContext(query="test query", trace_id="abc123")


class TestLinearExecution:
    @pytest.mark.asyncio
    async def test_follows_static_edges(self, context):
        workflow = (
            Workflow("linear")
            .add(RecordNode("first"), next_="second")
            .add(RecordNode("second"), next_="third")
            .add(RecordNode("third"), next_=END)
        )
        result = await workflow.run(context)

        assert result.completed
        assert result.trail == ["first", "second", "third"]
        assert result.steps == 3

    @pytest.mark.asyncio
    async def test_missing_edge_ends_the_run(self, context):
        workflow = Workflow("short").add(RecordNode("only"))
        result = await workflow.run(context)
        assert result.completed
        assert result.trail == ["only"]


class TestBranching:
    @pytest.mark.asyncio
    async def test_node_can_override_the_static_edge(self, context):
        """The property the router depends on: a node inspects state and
        names where to go, which an if/elif chain cannot express cleanly."""
        workflow = (
            Workflow("branching")
            .add(RecordNode("start", next_node="right"), next_="left")
            .add(RecordNode("left"), next_=END)
            .add(RecordNode("right"), next_=END)
        )
        result = await workflow.run(context)

        assert result.trail == ["start", "right"]
        assert "left" not in result.trail

    @pytest.mark.asyncio
    async def test_callable_edges_choose_at_runtime(self, context):
        context.set("target", "b")
        workflow = (
            Workflow("dynamic")
            .add(RecordNode("start"), next_=lambda ctx: ctx.get("target"))
            .add(RecordNode("a"), next_=END)
            .add(RecordNode("b"), next_=END)
        )
        result = await workflow.run(context)
        assert result.trail == ["start", "b"]

    @pytest.mark.asyncio
    async def test_halt_stops_without_completing(self, context):
        workflow = (
            Workflow("halting")
            .add(RecordNode("first"), next_="second")
            .add(RecordNode("second", halt=True), next_="third")
            .add(RecordNode("third"), next_=END)
        )
        result = await workflow.run(context)

        assert result.completed is False
        assert result.halted_at == "second"
        assert "third" not in result.trail


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_cycles_are_caught_rather_than_hanging(self, context):
        """Without the step cap this reads as a hang; with it, the error
        names the misconfiguration."""
        workflow = (
            Workflow("cyclic", max_steps=10)
            .add(RecordNode("ping"), next_="pong")
            .add(RecordNode("pong"), next_="ping")
        )
        with pytest.raises(WorkflowError, match="cycle"):
            await workflow.run(context)

    @pytest.mark.asyncio
    async def test_unknown_node_name_is_an_explicit_error(self, context):
        workflow = Workflow("broken").add(RecordNode("start"), next_="nowhere")
        with pytest.raises(WorkflowError, match="no node named"):
            await workflow.run(context)

    @pytest.mark.asyncio
    async def test_node_exceptions_propagate(self, context):
        """A failed node leaves the context half-built; continuing would
        assemble a result from partial state."""
        workflow = Workflow("failing").add(ExplodingNode())
        with pytest.raises(RuntimeError, match="node failed"):
            await workflow.run(context)

    @pytest.mark.asyncio
    async def test_empty_workflow_is_rejected(self, context):
        with pytest.raises(WorkflowError, match="no nodes"):
            await Workflow("empty").run(context)

    def test_duplicate_node_names_are_rejected(self):
        """Silently overwriting would make load order decide which node runs."""
        workflow = Workflow("dupes").add(RecordNode("same"))
        with pytest.raises(ValueError, match="already has a node"):
            workflow.add(RecordNode("same"))


class TestContext:
    def test_require_names_the_missing_key_and_the_trail(self):
        context = WorkflowContext(query="q")
        context.trail.append("earlier_node")
        with pytest.raises(KeyError) as exc:
            context.require("never_set")
        assert "never_set" in str(exc.value)
        assert "earlier_node" in str(exc.value)

    def test_set_is_chainable(self):
        context = WorkflowContext(query="q")
        context.set("a", 1).set("b", 2)
        assert context.get("a") == 1
        assert context.get("b") == 2

    def test_errors_mark_the_context_failed(self):
        context = WorkflowContext(query="q")
        assert context.failed is False
        context.record_error("something broke")
        assert context.failed is True


class TestFunctionNodes:
    @pytest.mark.asyncio
    async def test_plain_functions_work_as_nodes(self, context):
        async def double(ctx: WorkflowContext) -> None:
            ctx.set("value", 21 * 2)

        workflow = Workflow("fn").add(double, name="double", next_=END)
        await workflow.run(context)
        assert context.get("value") == 42
