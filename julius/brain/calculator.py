"""Deliberately bounded numeric calculator, without eval/sympify."""

import ast
import math
import operator
import re
from typing import Any

OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_NUMBER = r"-?\d[\d,]*(?:\.\d+)?"
_PERCENT_OF = re.compile(rf"({_NUMBER})\s*(?:%|per\s?cent)\s+of\s+({_NUMBER}|\([^()]*\))", re.I)


def normalize_percent(expression: str) -> str:
    """Rewrite "X% of Y" / "X percent of Y" as (X/100)*(Y); a bare "%" stays the modulo operator."""

    def rewrite(match: Any) -> str:
        x, y = match.group(1).replace(",", ""), match.group(2).replace(",", "")
        return f"({x}/100)*({y})"

    return _PERCENT_OF.sub(rewrite, expression)


def evaluate(expression: str) -> int | float:
    if not isinstance(expression, str) or len(expression) > 512:
        raise ValueError("Use a math expression of at most 512 characters.")
    tree = ast.parse(normalize_percent(expression).strip(), mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("That expression is too complex.")

    def visit(node: Any) -> Any:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = node.value
        elif isinstance(node, ast.Name) and node.id in {"pi", "e"}:
            value = getattr(math, node.id)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in OPS:
            a, b = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and abs(b) > 100:
                raise ValueError("Exponent must be between -100 and 100.")
            value = OPS[type(node.op)](a, b)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"sqrt", "abs"}
            and len(node.args) == 1
            and not node.keywords
        ):
            value = (math.sqrt if node.func.id == "sqrt" else abs)(visit(node.args[0]))
        else:
            raise ValueError("Unsupported math. Use arithmetic, sqrt, abs, pi or e.")
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e100:
            raise ValueError("Result exceeds the supported numeric range.")
        return value

    return visit(tree.body)
