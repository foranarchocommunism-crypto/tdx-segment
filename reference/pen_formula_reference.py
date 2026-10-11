"""Evaluate the frozen legacy formula independently of the C++ implementation.

Only the pen formula's primitives are supported. Host boundary conventions are
explicit in docs/pen-migration.md; this is not a substitute for a TDX host run.
"""
from __future__ import annotations

import ast
import math
import re
import struct
from pathlib import Path


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def evaluate(highs: list[float], lows: list[float], closes: list[float], days: list[float], formula: Path) -> dict[str, list[float]]:
    size = len(highs)
    if not size or len({len(x) for x in (highs, lows, closes, days)}) != 1:
        raise ValueError("Expected four nonempty, equally sized input arrays")
    env = dict(zip(("H", "L", "C", "DAY"), ([f32(v) for v in x] for x in (highs, lows, closes, days))))
    nan = math.nan

    def vector(x):
        return x if isinstance(x, list) else [float(x)] * size

    def truth(x):
        return math.isfinite(x) and x != 0

    def binary(a, b, operation):
        return [f32(operation(x, y)) if math.isfinite(x) and math.isfinite(y) else nan for x, y in zip(vector(a), vector(b))]

    def call(name, arguments):
        x = vector(arguments[0])
        if name == "ISVALID":
            return [float(math.isfinite(v)) for v in x]
        if name == "IF":
            yes, no = map(vector, arguments[1:])
            return [(a if truth(c) else b) if math.isfinite(c) else nan for c, a, b in zip(x, yes, no)]
        if name == "BARSLAST":
            out, last = [], -1
            for i, v in enumerate(x):
                if truth(v):
                    last = i
                out.append(float(i - last))
            return out
        n = vector(arguments[1])
        if name == "REF":
            return [x[i-int(v)] if math.isfinite(v) and 0 <= v <= i else nan for i, v in enumerate(n)]
        if name == "BACKSET":
            out = [0.] * size
            for i, (v, width) in enumerate(zip(x, n)):
                if truth(v):
                    out[max(0, i-int(width)+1):i+1] = [1.] * min(i+1, int(width))
            return out
        out = []
        for i, width in enumerate(n):
            if not math.isfinite(width):
                out.append(nan)
                continue
            start = 0 if width <= 0 else max(0, i-int(width)+1)
            window = x[start:i+1]
            valid = [v for v in window if math.isfinite(v)]
            if name == "MA":
                # Full window, double accumulation, final binary32 rounding.
                out.append(f32(sum(reversed(window)) / width) if len(window) == width and len(valid) == len(window) else nan)
            elif name == "HHV":
                out.append(max(valid) if valid else nan)
            elif name == "LLV":
                out.append(min(valid) if valid else nan)
            elif name == "COUNT":
                out.append(float(sum(truth(v) for v in window)))
            else:
                raise ValueError(f"Unsupported function {name}")
        return out

    def visit(node):
        if isinstance(node, ast.Name):
            return env[node.id]
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -visit(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return call(node.func.id, [visit(v) for v in node.args])
        if isinstance(node, ast.BinOp):
            operations = {ast.Add: lambda x, y: x+y, ast.Sub: lambda x, y: x-y}
            return binary(visit(node.left), visit(node.right), operations[type(node.op)])
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            operations = {ast.Eq: lambda x,y: x==y, ast.NotEq: lambda x,y: x!=y, ast.Gt: lambda x,y: x>y,
                          ast.Lt: lambda x,y: x<y, ast.GtE: lambda x,y: x>=y, ast.LtE: lambda x,y: x<=y}
            return binary(visit(node.left), visit(node.comparators[0]), operations[type(node.ops[0])])
        if isinstance(node, ast.BoolOp):
            result = visit(node.values[0])
            op = (lambda x,y: truth(x) and truth(y)) if isinstance(node.op, ast.And) else (lambda x,y: truth(x) or truth(y))
            for value in node.values[1:]:
                result = binary(result, visit(value), op)
            return result
        raise ValueError(f"Unsupported syntax: {ast.dump(node)}")

    source = re.sub(r"\{.*?\}", "", formula.read_text(encoding="utf-8"), flags=re.S)
    for assignment in source.split(";"):
        if not assignment.strip():
            continue
        name, expression = assignment.strip().split(":=", 1)
        expression = re.sub(r"(?<![!<>=])=(?!=)", "==", expression).replace("AND", "and").replace("OR", "or")
        env[name] = vector(visit(ast.parse(expression.strip(), mode="eval").body))
    return env
