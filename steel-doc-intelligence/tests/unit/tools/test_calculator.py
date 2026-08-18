from __future__ import annotations

import pytest

from src.tools.builtin.calculator import CalculatorError, evaluate, looks_arithmetic


class TestArithmetic:
    @pytest.mark.parametrize(
        "expression,expected",
        [
            ("2 + 2", 4),
            ("12 * 7", 84),
            ("100 / 4", 25),
            ("10 - 3 * 2", 4),
            ("(10 - 3) * 2", 14),
            ("2 ** 8", 256),
            ("2^8", 256),
            ("17 % 5", 2),
            ("17 // 5", 3),
            ("-5 + 3", -2),
        ],
    )
    def test_evaluates_arithmetic(self, expression, expected):
        assert evaluate(expression) == expected

    @pytest.mark.parametrize(
        "expression,expected",
        [("sqrt(144)", 12), ("abs(-7)", 7), ("round(3.7)", 4), ("max(3, 9)", 9)],
    )
    def test_evaluates_allowed_functions(self, expression, expected):
        assert evaluate(expression) == expected

    def test_constants_resolve(self):
        assert evaluate("pi") == pytest.approx(3.14159, rel=1e-4)


class TestCodeExecutionIsImpossible:
    """The reason this tool parses an AST instead of calling eval().

    Every one of these is a valid Python expression. Under eval() on
    LLM-influenced text, each is remote code execution.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            "__import__('os').system('echo pwned')",
            "open('/etc/passwd').read()",
            "().__class__.__bases__[0].__subclasses__()",
            "exec('x=1')",
            "eval('1+1')",
            "globals()",
            "[x for x in range(10)]",
            "lambda: 1",
            "'a' * 10**9",
        ],
    )
    def test_code_payloads_are_rejected(self, payload):
        with pytest.raises(CalculatorError):
            evaluate(payload)

    def test_attribute_access_is_rejected(self):
        with pytest.raises(CalculatorError):
            evaluate("(1).__class__")

    def test_unknown_names_are_rejected(self):
        with pytest.raises(CalculatorError):
            evaluate("os")


class TestResourceLimits:
    def test_huge_exponent_is_refused(self):
        """`2 ** 10**9` is trivially typed and would allocate until the
        process dies."""
        with pytest.raises(CalculatorError):
            evaluate("2 ** 10000000")

    def test_huge_factorial_is_refused(self):
        with pytest.raises(CalculatorError):
            evaluate("factorial(999999)")

    def test_overlong_expression_is_refused(self):
        with pytest.raises(CalculatorError):
            evaluate("1+" * 400 + "1")

    def test_division_by_zero_is_a_clean_error(self):
        with pytest.raises(CalculatorError, match="Division by zero"):
            evaluate("1/0")

    def test_empty_expression_is_a_clean_error(self):
        with pytest.raises(CalculatorError):
            evaluate("")


class TestArithmeticDetection:
    @pytest.mark.parametrize("query", ["12 * 7", "what is 45 + 55?", "2^10", "100/4"])
    def test_recognises_arithmetic(self, query):
        assert looks_arithmetic(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "what is our refund policy",
            "compare Q1 and Q2 revenue",
            "section 3.2",
            "hello",
            "",
        ],
    )
    def test_rejects_prose(self, query):
        assert looks_arithmetic(query) is False

    def test_requires_both_a_digit_and_an_operator(self):
        assert looks_arithmetic("12 34") is False
        assert looks_arithmetic("a + b") is False
