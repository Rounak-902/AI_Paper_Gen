#!/usr/bin/env python3
"""Pre-flight .env validator for PaperGen.

Mirrors app/core/config.py's Settings validators WITHOUT importing pydantic
(the project venv is a Windows venv, so pydantic_core's .pyd cannot be imported
on Linux). To avoid drifting from the real rules, the constants and the
required/optional field list are extracted from config.py via AST parsing
rather than hand-copied.

Never prints secret values -- only lengths and pass/fail.
"""
from __future__ import annotations

import ast
import pathlib
import sys

SECRET_FIELDS = {
    "DATABASE_URL",
    "JWT_SECRET",
    "JWT_REFRESH_SECRET",
    "GEMINI_API_KEY",
    "STORAGE_SECRET_KEY",
    "MATHPIX_APP_KEY",
    "SENTRY_DSN",
    "REDIS_URL",
    "QUEUE_REDIS_URL",
}


def load_config_facts(config_path: pathlib.Path):
    """Extract constants + field requiredness from config.py using AST."""
    tree = ast.parse(config_path.read_text(encoding="utf-8"))

    consts: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and node.value is not None:
                    try:
                        consts[t.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass

    settings_cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "Settings"
    )

    required: list[str] = []
    optional: list[str] = []
    annotations: dict[str, str] = {}
    for stmt in settings_cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            name = stmt.target.id
            if name == "model_config":
                continue
            annotations[name] = ast.unparse(stmt.annotation)
            (optional if stmt.value is not None else required).append(name)

    return consts, required, optional, annotations


def parse_env(path: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def show(field: str, value: str) -> str:
    """Redact secrets; show benign values verbatim."""
    if field in SECRET_FIELDS and value:
        return f"<set, {len(value)} chars>"
    return repr(value)


def main() -> int:
    backend = pathlib.Path(__file__).resolve().parent
    config_path = backend / "app" / "core" / "config.py"
    env_path = backend / ".env"

    if not config_path.exists():
        print(f"FATAL: {config_path} not found")
        return 2
    if not env_path.exists():
        print(f"FATAL: {env_path} not found")
        return 2

    consts, required, optional, annotations = load_config_facts(config_path)
    placeholders: tuple[str, ...] = tuple(consts["PLACEHOLDER_SUBSTRINGS"])
    min_len: int = int(consts["MIN_SECRET_LENGTH"])

    print("Rules extracted from app/core/config.py via AST:")
    print(f"  MIN_SECRET_LENGTH   = {min_len}")
    print(f"  PLACEHOLDER_SUBSTRINGS ({len(placeholders)}) = {placeholders}")
    print(f"  required fields (no default) = {required}")
    print()

    env = parse_env(env_path)
    failures: list[str] = []
    warnings: list[str] = []

    print("=" * 78)
    print("REQUIRED FIELDS")
    print("=" * 78)

    def hit(value: str) -> str | None:
        low = value.strip().lower()
        for p in placeholders:
            if p in low:
                return p
        return None

    for f in required:
        raw = env.get(f)
        if raw is None:
            print(f"  FAIL  {f}: absent from .env and has no default -> ValidationError (missing)")
            failures.append(f)
            continue
        if not raw.strip():
            print(f"  FAIL  {f}: empty")
            failures.append(f)
            continue
        p = hit(raw)
        if p:
            print(f"  FAIL  {f}: {show(f, raw)} contains placeholder {p!r}")
            failures.append(f)
            continue
        if f in ("JWT_SECRET", "JWT_REFRESH_SECRET") and len(raw.strip()) < min_len:
            print(f"  FAIL  {f}: too short ({len(raw.strip())} < {min_len})")
            failures.append(f)
            continue
        if f == "DATABASE_URL":
            valid = ("postgresql://", "postgresql+asyncpg://", "postgres://")
            if not any(raw.lower().startswith(v) for v in valid):
                print(f"  FAIL  {f}: scheme not in {valid}")
                failures.append(f)
                continue
        print(f"  PASS  {f}: {show(f, raw)}")

    # model_validator: distinct JWT secrets
    print()
    print("=" * 78)
    print("MODEL VALIDATOR")
    print("=" * 78)
    a, b = env.get("JWT_SECRET", ""), env.get("JWT_REFRESH_SECRET", "")
    if a and b and a == b:
        print("  FAIL  JWT_SECRET == JWT_REFRESH_SECRET (must be distinct)")
        failures.append("JWT_SECRET/JWT_REFRESH_SECRET distinctness")
    else:
        print("  PASS  JWT_SECRET != JWT_REFRESH_SECRET")

    # Type coercion sanity for optional fields present in .env
    print()
    print("=" * 78)
    print("TYPE COERCION OF OPTIONAL FIELDS PRESENT IN .env")
    print("=" * 78)
    bad_types: list[str] = []
    for f, ann in annotations.items():
        if f not in env or f in required:
            continue
        v = env[f]
        try:
            if ann == "int":
                int(v)
            elif ann == "float":
                float(v)
            elif ann == "bool":
                if v.lower() not in {
                    "1", "0", "true", "false", "yes", "no", "on", "off", "t", "f", "y", "n",
                }:
                    raise ValueError(f"{v!r} is not a bool pydantic accepts")
        except ValueError as e:
            print(f"  FAIL  {f}: {ann} <- {v!r} ({e})")
            bad_types.append(f)
    if not bad_types:
        print("  PASS  all int/float/bool values in .env coerce cleanly")
    failures.extend(bad_types)

    # Advisory: placeholders left in non-required fields
    print()
    print("=" * 78)
    print("ADVISORY: placeholder values remaining in OPTIONAL fields")
    print("=" * 78)
    for f in optional:
        if f in env and (p := hit(env[f])):
            print(f"  WARN  {f}: contains {p!r} (has a default/Optional, so boot still succeeds)")
            warnings.append(f)
    if not warnings:
        print("  none")

    print()
    print("=" * 78)
    if failures:
        print(f"RESULT: Settings() WOULD RAISE ValidationError -> {len(failures)} blocking issue(s):")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("RESULT: all required fields satisfy config.py's validators.")
    print(f"        ({len(warnings)} non-blocking placeholder warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
