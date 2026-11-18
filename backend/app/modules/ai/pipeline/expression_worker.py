"""Isolated, closed SymPy arithmetic language. No eval/sympify of model text."""
from __future__ import annotations

import ast
import json
import math
import os
import sys


def limits():
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    elif os.name == "nt":
        import ctypes
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read", "write", "other", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.GetCurrentProcess.restype = w.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x100 | 0x2  # process-memory and per-process CPU time
        info.basic.process_time = 20_000_000  # 2 seconds, in 100ns units
        info.process_memory = 256 * 1024 * 1024
        if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise RuntimeError("cannot enforce sandbox memory/CPU limits")
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise RuntimeError("cannot assign sandbox limits")
    else:
        raise RuntimeError("unsupported sandbox platform")


def main():
    import sympy as s
    limits()
    # Force SymPy's mpmath integration to fully initialize in the trusted
    # startup phase, before the audit hook denies file-open events.
    # sympy.sin/cos delegate to mpmath for numeric evaluation; both SymPy's
    # dispatch layer and mpmath itself lazy-load data files on first use.
    # Merely importing mpmath is insufficient — SymPy's internal integration
    # path must also be exercised.  Warmup values must be non-trivial:
    # sin(0)/cos(0) evaluate to exact SymPy integers without invoking mpmath,
    # so they skip the lazy-loading path.  sin(pi/6)/cos(pi/6) produce
    # Rational results that exercise the full dispatch chain.  This is safe:
    # no user input has been read yet (stdin read is below), so no model-
    # supplied expression can influence what gets loaded here.
    float(s.sin(s.pi / 6)); float(s.cos(s.pi / 6))  # noqa: E702 — warmup
    # Deny all I/O after trusted startup.  The audit hook covers open, socket,
    # subprocess, os.system and os.spawn events.  It cannot be removed once
    # installed (Python provides no removeaudithook API).
    def deny(event, args):
        if event == "open" or event.startswith(("socket.", "subprocess.", "os.system", "os.spawn")):
            raise PermissionError("sandbox I/O forbidden")
    sys.addaudithook(deny)
    print("READY", flush=True)
    expression = json.loads(sys.stdin.read(4096))["expression"]
    if not isinstance(expression, str) or len(expression) > 512:
        raise ValueError("expression too large")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("expression too complex")
    functions = {"sqrt": s.sqrt, "sin": s.sin, "cos": s.cos, "abs": s.Abs,
                 "Rational": s.Rational, "floor": s.floor, "ceiling": s.ceiling}

    def visit(node, depth=0):
        if depth > 20:
            raise ValueError("expression nesting limit")
        recur = lambda n: visit(n, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if not math.isfinite(node.value) or abs(node.value) > 1e12:
                raise ValueError("literal limit")
            return s.Rational(str(node.value))
        if isinstance(node, ast.Name) and node.id in ("pi", "E"):
            return getattr(s, node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = recur(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            a, b = recur(node.left), recur(node.right)
            if isinstance(node.op, ast.Add): value = a + b
            elif isinstance(node.op, ast.Sub): value = a - b
            elif isinstance(node.op, ast.Mult): value = a * b
            elif isinstance(node.op, ast.Div): value = a / b
            elif isinstance(node.op, ast.Pow) and abs(float(b)) <= 12: value = a ** b
            else: raise ValueError("unsupported operator or exponent")
            if value.is_real is not True or value.is_finite is not True or abs(float(value)) > 1e24:
                raise ValueError("intermediate limit")
            return value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
            if node.keywords or not 1 <= len(node.args) <= 2:
                raise ValueError("invalid function arguments")
            return functions[node.func.id](*(recur(n) for n in node.args))
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            a, b = recur(node.left), recur(node.comparators[0])
            op = node.ops[0]
            if isinstance(op, ast.Eq): return bool(a == b)
            if isinstance(op, ast.NotEq): return bool(a != b)
            if isinstance(op, ast.Lt): return bool(a < b)
            if isinstance(op, ast.LtE): return bool(a <= b)
            if isinstance(op, ast.Gt): return bool(a > b)
            if isinstance(op, ast.GtE): return bool(a >= b)
        raise ValueError("node outside closed arithmetic language")

    value = visit(tree.body)
    result = value if type(value) is bool else float(value)
    if type(result) is not bool and not math.isfinite(result):
        raise ValueError("nonfinite result")
    print(json.dumps({"value": result, "resource_limits": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}))
        sys.exit(1)
