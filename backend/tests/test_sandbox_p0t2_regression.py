"""P0-T2 fixture regression: replay original 55 spike fixtures against production worker.

Confirms no regression from spike to production. The production expression_worker.py
uses a tighter grammar (7 functions vs spike's ~30), so some expressions will be
EXPECTEDLY rejected. Each case is documented.

The spike used sympy.parse_expr with: sqrt, cbrt, Abs/abs, sign, floor, ceiling,
Rational, Integer, Float, Symbol, exp, log/ln, log10, log2, Pow/pow, sin, cos, tan,
cot, sec, csc, asin, acos, atan, atan2, sinh, cosh, tanh, factorial, binomial,
Max, Min, gcd, lcm, simplify, expand, factor, solve, isprime, Mod.

The production worker allows only: sqrt, sin, cos, abs, Rational, floor, ceiling.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from app.modules.ai.pipeline.validation import compute

FIXTURES_PATH = Path(__file__).parents[1] / ".." / "spikes" / "answer_verification" / "fixtures.json"

# Functions supported by the production worker's closed grammar
PRODUCTION_FUNCTIONS = {"sqrt", "sin", "cos", "abs", "Rational", "floor", "ceiling"}

# Functions the spike supported but production does NOT
SPIKE_ONLY_FUNCTIONS = {
    "cbrt", "Abs", "sign", "Integer", "Float", "Symbol", "symbols", "Function",
    "exp", "log", "ln", "log10", "log2", "Pow", "pow", "tan", "cot", "sec", "csc",
    "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh",
    "factorial", "binomial", "Max", "Min", "gcd", "lcm",
    "simplify", "expand", "factor", "solve", "isprime", "Mod",
    "S", "ceil",
}


def _uses_unsupported_function(expression: str) -> set[str]:
    """Check if expression uses functions not in the production whitelist."""
    found = set()
    for fn in SPIKE_ONLY_FUNCTIONS:
        if fn + "(" in expression or fn + " " in expression:
            found.add(fn)
    return found


def load_fixtures():
    """Load the original P0-T2 fixture file."""
    path = FIXTURES_PATH.resolve()
    if not path.exists():
        pytest.skip(f"P0-T2 fixtures not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def fixtures():
    return load_fixtures()


async def test_fixture_count(fixtures):
    """Confirm we loaded the expected 55 fixtures."""
    assert len(fixtures) == 55, f"Expected 55 fixtures, got {len(fixtures)}"
    print(f"P0T2_REGRESSION_LOADED: {len(fixtures)} fixtures")


async def test_production_compatible_fixtures(fixtures):
    """Run all fixtures whose expressions use only production-supported functions.

    After the mpmath warmup fix, sin/cos now work. All compatible expressions
    (those not using spike-only functions like factorial/log/exp/tan) should pass.
    """
    compatible = []
    incompatible = []

    for q in fixtures:
        expr = q.get("solution_expression")
        if not expr or str(expr).lower() in ("null", "none", ""):
            continue
        expr_str = str(expr)
        unsupported = _uses_unsupported_function(expr_str)
        if unsupported:
            incompatible.append((q["id"], expr_str, unsupported))
        else:
            compatible.append(q)

    print(f"\nP0T2_REGRESSION_COMPATIBLE: {len(compatible)} expressions use production grammar")
    print(f"P0T2_REGRESSION_INCOMPATIBLE: {len(incompatible)} expressions use spike-only functions")

    for qid, expr, fns in incompatible:
        print(f"  SPIKE_ONLY: {qid} expr={expr!r} uses={fns}")

    passed = 0
    failed = 0
    for q in compatible:
        expr = str(q["solution_expression"])
        stated = q.get("answer")
        tolerance = q.get("tolerance", 0.01)
        qid = q["id"]

        try:
            computed = await compute(expr)

            if q.get("qtype") == "mcq":
                print(f"  PASS: {qid} expr={expr!r} computed={computed} (MCQ, no answer comparison)")
                passed += 1
                continue

            stated_float = float(stated)
            if abs(computed - stated_float) <= tolerance:
                print(f"  PASS: {qid} expr={expr!r} computed={computed} stated={stated_float} delta={abs(computed - stated_float):.6f}")
                passed += 1
            else:
                print(f"  MISMATCH: {qid} expr={expr!r} computed={computed} stated={stated_float} delta={abs(computed - stated_float):.6f}")
                failed += 1
        except Exception as e:
            print(f"  UNEXPECTED_REJECT: {qid} expr={expr!r} error={e!r}")
            failed += 1

    print(f"\nP0T2_REGRESSION_RESULT: {passed}/{len(compatible)} compatible passed, {failed} unexpected failures")
    assert failed == 0, f"{failed} compatible expressions unexpectedly failed (see output above)"


async def test_incompatible_fixtures_documented(fixtures):
    """Document expressions that use spike-only functions — these are expected rejections."""
    incompatible_count = 0
    for q in fixtures:
        expr = q.get("solution_expression")
        if not expr or str(expr).lower() in ("null", "none", ""):
            continue
        unsupported = _uses_unsupported_function(str(expr))
        if unsupported:
            incompatible_count += 1
            # Verify production actually rejects them
            with pytest.raises(Exception):
                await compute(str(expr))

    print(f"P0T2_REGRESSION_INCOMPATIBLE_VERIFIED: all {incompatible_count} spike-only expressions correctly rejected by production grammar")
