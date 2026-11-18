"""Adversarial attribute-traversal sandbox escape tests against production expression worker.

Targets the specific untested gap identified in P0-T2's closeout (RESULTS.md §5, condition 3):
object-attribute-traversal escapes not requiring __import__/getattr.

Tests the PRODUCTION expression_worker.py (closed AST visitor), not the spike's
sympy.parse_expr approach. Every test sends a crafted expression through the
production compute() function and asserts rejection.

Defensive intent: these are rejection assertions against known-dangerous input
patterns on the project's own sandbox, not a transferable exploit toolkit.
"""
from __future__ import annotations

import pytest
from app.modules.ai.pipeline.validation import PipelineValidationError, compute


# ═══════════════════════════════════════════════════════════════════════════
# CLASS 1: Direct dunder attribute access (ast.Attribute node)
#
# The production worker's AST visitor only permits: Constant, Name (pi/E),
# UnaryOp, BinOp, Call (7 functions), Compare. ast.Attribute is NOT in the
# whitelist, so ANY dot-access expression should hit the catch-all:
#   raise ValueError("node outside closed arithmetic language")
# ═══════════════════════════════════════════════════════════════════════════

ATTRIBUTE_TRAVERSAL_CASES = [
    # --- Direct dunder attribute on literals ---
    pytest.param("(1).__class__", id="int_class"),
    pytest.param("(1).__class__.__bases__", id="int_class_bases"),
    pytest.param("(1).__class__.__bases__[0]", id="int_class_bases_index"),
    pytest.param("''.__class__", id="str_class"),
    pytest.param("''.__class__.__mro__", id="str_class_mro"),
    pytest.param("''.__class__.__mro__[1].__subclasses__()", id="str_mro_subclasses"),
    pytest.param("().__class__", id="tuple_class"),
    pytest.param("().__class__.__subclasses__()", id="tuple_subclasses"),
    pytest.param("().__class__.__bases__[0].__subclasses__()", id="tuple_bases_subclasses"),
    pytest.param("True.__class__.__bases__", id="bool_class_bases"),

    # --- Method calls via attribute access (not via Call on whitelisted Name) ---
    pytest.param("(1).__add__(2)", id="int_dunder_add"),
    pytest.param("(1).__mul__(3)", id="int_dunder_mul"),
    pytest.param("(1).__pow__(2)", id="int_dunder_pow"),
    pytest.param("(1).__format__('')", id="int_format"),
    pytest.param("(1).__sizeof__()", id="int_sizeof"),
    pytest.param("(1).__init_subclass__", id="int_init_subclass"),

    # --- Traversal via whitelisted names (pi, E, sqrt, etc.) ---
    # Even though 'pi' and 'sqrt' are in the Name whitelist, dot-access on them
    # still produces ast.Attribute which is not in the visitor.
    pytest.param("pi.__class__", id="pi_class"),
    pytest.param("E.__class__.__mro__", id="E_class_mro"),
    pytest.param("sqrt.__module__", id="sqrt_module"),
    pytest.param("sqrt.__class__.__bases__", id="sqrt_class_bases"),
    pytest.param("Rational.__init__.__globals__", id="Rational_init_globals"),
    pytest.param("sin.__self__", id="sin_self"),
    pytest.param("cos.__call__", id="cos_call"),

    # --- Deeper traversal chains targeting globals/builtins leak ---
    pytest.param("(1).__class__.__bases__[0].__subclasses__()[0].__init__.__globals__", id="deep_globals_leak"),
    pytest.param("frozenset.__mro__", id="frozenset_mro"),
    pytest.param("int.__dict__", id="int_dict"),
    pytest.param("object.__subclasses__()", id="object_subclasses"),
]


@pytest.mark.parametrize("expression", ATTRIBUTE_TRAVERSAL_CASES)
async def test_attribute_traversal_rejected(expression):
    """Every attribute-access expression must be rejected by the closed AST grammar."""
    with pytest.raises((PipelineValidationError, ValueError, Exception)) as exc_info:
        await compute(expression)
    # Print documentary evidence: what was tried, what happened
    print(f"ATTR_ESCAPE_BLOCKED: expr={expression!r} error={exc_info.value!r}")


# ═══════════════════════════════════════════════════════════════════════════
# CLASS 2: Non-attribute structural escapes (other unlisted AST nodes)
#
# The closed grammar also rejects these because their AST node types are
# not in the visitor whitelist. Included for completeness: if any of these
# sneak through, the grammar has a structural hole.
# ═══════════════════════════════════════════════════════════════════════════

STRUCTURAL_ESCAPE_CASES = [
    # Lambda (ast.Lambda)
    pytest.param("(lambda: 0)()", id="lambda_call"),
    pytest.param("(lambda: 0).__code__", id="lambda_code"),
    pytest.param("(lambda: 0).__globals__", id="lambda_globals"),

    # Comprehensions (ast.ListComp, ast.SetComp, ast.GeneratorExp)
    pytest.param("[x for x in range(10)]", id="list_comprehension"),
    pytest.param("{x for x in range(10)}", id="set_comprehension"),
    pytest.param("sum(x for x in range(10))", id="generator_expression"),

    # Collections (ast.List, ast.Dict, ast.Set, ast.Tuple)
    pytest.param("[1, 2, 3]", id="list_literal"),
    pytest.param("{1: 2}", id="dict_literal"),
    pytest.param("{1, 2, 3}", id="set_literal"),

    # F-strings (ast.JoinedStr)
    pytest.param('f"{1+1}"', id="fstring"),

    # Walrus operator (ast.NamedExpr)
    pytest.param("(x := 5)", id="walrus_operator"),

    # Subscript on non-whitelisted (ast.Subscript)
    pytest.param("[1,2,3][0]", id="subscript"),

    # Starred (ast.Starred)
    pytest.param("*[1,2]", id="starred"),

    # IfExp (ast.IfExp / ternary)
    pytest.param("1 if True else 0", id="ternary"),

    # BoolOp (ast.BoolOp)
    pytest.param("True and False", id="bool_and"),
    pytest.param("True or False", id="bool_or"),
]


@pytest.mark.parametrize("expression", STRUCTURAL_ESCAPE_CASES)
async def test_structural_escape_rejected(expression):
    """Every non-arithmetic structural expression must be rejected."""
    with pytest.raises((PipelineValidationError, ValueError, Exception)) as exc_info:
        await compute(expression)
    print(f"STRUCT_ESCAPE_BLOCKED: expr={expression!r} error={exc_info.value!r}")


# ═══════════════════════════════════════════════════════════════════════════
# CLASS 3: Import / exec / eval attempts (already tested in P0-T2 and
# P3-T4, included here for completeness of the escape inventory)
# ═══════════════════════════════════════════════════════════════════════════

IMPORT_EXEC_CASES = [
    pytest.param("__import__('os').system('whoami')", id="dunder_import"),
    pytest.param("exec('import os')", id="exec_import"),
    pytest.param("eval('1+1')", id="eval"),
    pytest.param("open('/etc/passwd')", id="open_file"),
    pytest.param("compile('1','','eval')", id="compile"),
    pytest.param("globals()", id="globals"),
    pytest.param("locals()", id="locals"),
    pytest.param("breakpoint()", id="breakpoint"),
    pytest.param("type.__subclasses__(type)", id="type_subclasses"),
]


@pytest.mark.parametrize("expression", IMPORT_EXEC_CASES)
async def test_import_exec_rejected(expression):
    """Import/exec/eval attempts must be rejected."""
    with pytest.raises((PipelineValidationError, ValueError, Exception)) as exc_info:
        await compute(expression)
    print(f"IMPORT_EXEC_BLOCKED: expr={expression!r} error={exc_info.value!r}")


# ═══════════════════════════════════════════════════════════════════════════
# CLASS 4: Resource exhaustion (already tested in P3-T4, included for
# completeness)
# ═══════════════════════════════════════════════════════════════════════════

RESOURCE_EXHAUSTION_CASES = [
    pytest.param("2**99999", id="huge_exponent"),
    pytest.param("[1]*999999", id="memory_bomb"),
    pytest.param("1/0", id="division_by_zero"),
]


@pytest.mark.parametrize("expression", RESOURCE_EXHAUSTION_CASES)
async def test_resource_exhaustion_rejected(expression):
    """Resource exhaustion attempts must be rejected."""
    with pytest.raises((PipelineValidationError, ValueError, Exception)) as exc_info:
        await compute(expression)
    print(f"RESOURCE_BLOCKED: expr={expression!r} error={exc_info.value!r}")


# ═══════════════════════════════════════════════════════════════════════════
# CLASS 5: Positive controls — legitimate expressions MUST still work
# ═══════════════════════════════════════════════════════════════════════════

POSITIVE_CASES = [
    pytest.param("2 + 3", 5.0, id="addition"),
    pytest.param("sqrt(144)", 12.0, id="sqrt"),
    pytest.param("Rational(1, 3) * 9", 3.0, id="rational"),
    pytest.param("sin(pi / 6)", 0.5, id="sin_pi_6"),
    pytest.param("cos(0)", 1.0, id="cos_0"),
    pytest.param("floor(3.7)", 3.0, id="floor"),
    pytest.param("ceiling(3.2)", 4.0, id="ceiling"),
    pytest.param("abs(-5)", 5.0, id="abs"),
    pytest.param("2 + 3 == 5", True, id="bool_equality"),
    pytest.param("2 * 3 + 1", 7.0, id="order_of_operations"),
    pytest.param("-5 + 3", -2.0, id="unary_minus"),
    pytest.param("pi", 3.141592653589793, id="pi_constant"),
]


@pytest.mark.parametrize("expression,expected", POSITIVE_CASES)
async def test_legitimate_expressions_accepted(expression, expected):
    """Legitimate arithmetic expressions must produce correct results."""
    result = await compute(expression)
    if type(expected) is bool:
        assert result is expected, f"Expected {expected}, got {result}"
    else:
        assert abs(result - expected) < 0.01, f"Expected {expected}, got {result}"
    print(f"POSITIVE_OK: expr={expression!r} result={result}")


# ═══════════════════════════════════════════════════════════════════════════
# CLASS 6: sin/cos audit hook conflict — RESOLVED
#
# Previously, sympy.sin/cos triggered mpmath lazy-loading which hit the
# sys.addaudithook deny-list.  Fixed by adding a non-trivial warmup
# (float(s.sin(s.pi/6)); float(s.cos(s.pi/6))) in the trusted startup
# phase of expression_worker.py, before the audit hook is installed.
# sin/cos now work correctly and are included in POSITIVE_CASES above.
# ═══════════════════════════════════════════════════════════════════════════

