from __future__ import annotations

import ast
import math
import operator
import re
from typing import Any

from src.tools.base import Tool, ToolError, ToolRequest, ToolResult
from src.tools.registry import tools

# Only these node types may appear. An allow-list, not a deny-list: a
# deny-list of dangerous nodes is a list you will forget to update, and the
# input here is user text that has usually passed through an LLM, so it is
# untrusted twice over.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Tuple,
    # Operators
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
)

_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}

_FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "pow": math.pow,
    "exp": math.exp,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "degrees": math.degrees,
    "radians": math.radians,
}

_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}

# Bounds that stop a one-line expression from becoming a denial of service.
# `2 ** 10**9` is trivially typed and would allocate until the process dies.
_MAX_EXPRESSION_CHARS = 500
_MAX_POWER_EXPONENT = 1000
_MAX_FACTORIAL_INPUT = 1000

_EXPRESSION_RE = re.compile(r"[-+*/%^().,\d\s]|\b(?:" + "|".join(_FUNCTIONS) + r"|pi|e|tau)\b")


class CalculatorError(ToolError):
    pass


def evaluate(expression: str) -> float | int:
    """Evaluate an arithmetic expression without executing arbitrary code.

    Uses `ast.parse(mode="eval")` plus an explicit node allow-list rather
    than `eval()`. `eval()` on LLM-influenced text is remote code execution:
    `__import__("os").system(...)` is a valid Python expression. Parsing to
    an AST and walking only the arithmetic nodes makes that unrepresentable
    rather than merely discouraged.
    """
    if not expression or not expression.strip():
        raise CalculatorError("Empty expression.")
    if len(expression) > _MAX_EXPRESSION_CHARS:
        raise CalculatorError(f"Expression exceeds {_MAX_EXPRESSION_CHARS} characters.")

    # `^` reads as exponentiation to most people but is XOR in Python, and
    # XOR is not in the allow-list, so it would fail confusingly.
    normalised = expression.strip().replace("^", "**").rstrip("=").strip()

    try:
        tree = ast.parse(normalised, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"Could not parse {expression!r} as arithmetic.") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise CalculatorError(
                f"{type(node).__name__} is not allowed in an arithmetic expression."
            )

    result = _eval_node(tree.body)
    if isinstance(result, complex):
        raise CalculatorError("Complex results are not supported.")
    if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
        raise CalculatorError("Result is not a finite number.")
    return result


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("Only numeric literals are allowed.")
        return node.value

    if isinstance(node, ast.Name):
        if node.id not in _CONSTANTS:
            raise CalculatorError(f"Unknown name {node.id!r}.")
        return _CONSTANTS[node.id]

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"Unsupported unary operator {type(node.op).__name__}.")
        return op(_eval_node(node.operand))

    if isinstance(node, ast.BinOp):
        op = _BINARY_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"Unsupported operator {type(node.op).__name__}.")
        left, right = _eval_node(node.left), _eval_node(node.right)

        if isinstance(node.op, ast.Pow) and abs(_as_number(right)) > _MAX_POWER_EXPONENT:
            raise CalculatorError(f"Exponent above {_MAX_POWER_EXPONENT} is not allowed.")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and _as_number(right) == 0:
            raise CalculatorError("Division by zero.")
        return op(left, right)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise CalculatorError("Only direct calls to allowed functions are permitted.")
        func = _FUNCTIONS.get(node.func.id)
        if func is None:
            raise CalculatorError(f"Unknown function {node.func.id!r}.")
        if node.keywords:
            raise CalculatorError("Keyword arguments are not supported.")

        args = [_eval_node(arg) for arg in node.args]
        if node.func.id == "factorial" and (
            not args or _as_number(args[0]) > _MAX_FACTORIAL_INPUT
        ):
            raise CalculatorError(f"factorial() is limited to {_MAX_FACTORIAL_INPUT}.")
        return func(*args)

    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(item) for item in node.elts)

    raise CalculatorError(f"{type(node).__name__} is not allowed.")


def _as_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raise CalculatorError("Expected a number.")


def looks_arithmetic(text: str) -> bool:
    """Cheap pre-check used by the router's rule layer.

    Requires a digit, an operator and no unmatched alphabetic runs, so
    "what is 12 * 7" matches while "compare Q1 and Q2 revenue" does not.
    Deliberately conservative: a false negative costs one unnecessary RAG
    call, a false positive gives the user an arithmetic error instead of an
    answer.
    """
    stripped = text.strip().rstrip("?=").strip()
    stripped = re.sub(r"^(what\s+is|calculate|compute|solve|how\s+much\s+is)\s+", "", stripped, flags=re.I)
    if not stripped or not any(ch.isdigit() for ch in stripped):
        return False
    if not any(op in stripped for op in "+-*/%^"):
        return False
    # Every remaining alphabetic run must be an allowed function or constant.
    for word in re.findall(r"[A-Za-z_]+", stripped):
        if word.lower() not in _FUNCTIONS and word.lower() not in _CONSTANTS:
            return False
    return True


class CalculatorTool(Tool):
    name = "calculator"
    description = (
        "Evaluate a self-contained arithmetic or mathematical expression "
        "(e.g. '12 * 7', 'sqrt(144) + 3', '15% of 200'). Use for pure "
        "computation that needs no documents."
    )
    requires_flag = "enable_calculator"

    async def execute(self, request: ToolRequest) -> ToolResult:
        expression = str(request.args.get("expression") or request.query)
        cleaned = _strip_prose(expression)
        value = evaluate(cleaned)
        formatted = _format(value)
        return ToolResult(
            answer=f"{cleaned.strip()} = {formatted}",
            data={"expression": cleaned.strip(), "result": value},
            # Arithmetic is deterministic, so caching it is safe and cheap.
            cacheable=True,
        )


def _strip_prose(text: str) -> str:
    """Pull the expression out of a natural-language wrapper.

    Also rewrites "X% of Y", which people write constantly and which is not
    valid arithmetic syntax.
    """
    stripped = text.strip().rstrip("?=").strip()
    stripped = re.sub(
        r"^(what\s+is|whats|what's|calculate|compute|solve|how\s+much\s+is|evaluate)\s+",
        "",
        stripped,
        flags=re.I,
    )
    percent_of = re.match(r"^([\d.]+)\s*%\s+of\s+([\d.]+)$", stripped, flags=re.I)
    if percent_of:
        return f"({percent_of.group(1)} / 100) * {percent_of.group(2)}"
    return stripped


def _format(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


@tools.register(
    "calculator",
    description=CalculatorTool.description,
    requires_flag=CalculatorTool.requires_flag,
    tags=("math", "offline"),
)
def _build_calculator() -> Tool:
    return CalculatorTool()
