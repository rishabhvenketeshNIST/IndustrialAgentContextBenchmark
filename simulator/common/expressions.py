"""Restricted arithmetic expressions used by configuration (coupling, demand, rules).

Expressions are parsed with :mod:`ast`, validated against a whitelist of node
types and functions, then compiled once. No attribute access, subscripting,
comprehensions, lambdas or imports are allowed.

Example::

    expr = Expression("clip(rated * eff * run, 0, design)", ["rated", "eff", "run", "design"])
    expr.evaluate({"rated": 700, "eff": 0.8, "run": 1, "design": 1000})
"""
from __future__ import annotations

import ast
import math
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _interp(x: float, xs: Sequence[float], ys: Sequence[float]) -> float:
    return float(np.interp(x, xs, ys))


def _step(x: float, threshold: float) -> float:
    return 1.0 if x >= threshold else 0.0


def _ramp(x: float, x0: float, x1: float) -> float:
    """0 below x0, 1 above x1, linear in between."""
    if x1 == x0:
        return _step(x, x0)
    return _clip((x - x0) / (x1 - x0), 0.0, 1.0)


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


FUNCTIONS = {
    "min": min, "max": max, "abs": abs, "clip": _clip, "sqrt": math.sqrt, "exp": math.exp,
    "log": math.log, "interp": _interp, "step": _step, "ramp": _ramp, "safe_div": _safe_div,
    "sum": lambda *a: float(sum(a[0])) if len(a) == 1 and isinstance(a[0], (list, tuple)) else float(sum(a)),
    "round": round, "float": float,
}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Call, ast.Name,
    ast.Load, ast.Constant, ast.Tuple, ast.List,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv, ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


class ExpressionError(ValueError):
    pass


class Expression:
    def __init__(self, source: str, variables: Optional[Iterable[str]] = None) -> None:
        self.source = str(source)
        try:
            tree = ast.parse(self.source, mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"Invalid expression '{self.source}': {exc.msg}") from exc
        names: List[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_NODES):
                raise ExpressionError(f"Disallowed syntax {type(node).__name__} in '{self.source}'")
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                    raise ExpressionError(f"Function not allowed in '{self.source}'")
                if node.keywords:
                    raise ExpressionError(f"Keyword arguments not allowed in '{self.source}'")
            if isinstance(node, ast.Name) and node.id not in FUNCTIONS and node.id not in ("True", "False"):
                names.append(node.id)
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
                raise ExpressionError(f"Only numeric constants allowed in '{self.source}'")
        self.names = sorted(set(names))
        if variables is not None:
            unknown = set(self.names) - set(variables)
            if unknown:
                raise ExpressionError(f"Unknown variable(s) {sorted(unknown)} in '{self.source}'")
        self._code = compile(tree, "<expr>", "eval")

    def evaluate(self, values: Dict[str, float]) -> float:
        env = dict(FUNCTIONS)
        env.update(values)
        try:
            result = eval(self._code, {"__builtins__": {}}, env)  # noqa: S307 - validated AST
        except ZeroDivisionError:
            return 0.0
        except Exception as exc:
            raise ExpressionError(f"Error evaluating '{self.source}' with {values}: {exc}") from exc
        if isinstance(result, bool):
            return 1.0 if result else 0.0
        return float(result)

    def __repr__(self) -> str:
        return f"Expression({self.source!r})"
