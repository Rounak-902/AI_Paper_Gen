#!/usr/bin/env python3
"""Convert a Neon (libpq-style) Postgres URL into the form SQLAlchemy+asyncpg needs,
then optionally write it into backend/.env as DATABASE_URL.

Reads the DSN from STDIN (not argv) so it does not land in shell history or a
process listing. Never prints the DSN or password.

Transformations, each justified against the pinned source in
backend/.venv/Lib/site-packages (asyncpg 0.31.0, SQLAlchemy 2.0.52):

  1. scheme -> postgresql+asyncpg://
     Project convention: pyproject pins SQLAlchemy+asyncpg, .env.example and
     tests/test_config.py both use this form. ('+asyncpg' is a SQLAlchemy
     dialect selector that SQLAlchemy strips before calling asyncpg.)

  2. drop channel_binding
     asyncpg does NOT recognise it and does NOT error: connect_utils.py assigns
     leftover query keys to `server_settings`, which are sent as Postgres GUCs in
     the startup packet. The server then rejects the connection with
     'unrecognized configuration parameter "channel_binding"'.

  3. sslmode=<v>  ->  ssl=<v>
     SQLAlchemy's asyncpg dialect does `opts.update(url.query)` in
     create_connect_args, hoisting every query param into kwargs for
     asyncpg.connect(). That signature exposes `ssl=`, has no `sslmode=` and no
     **kwargs, and the dialect performs no ssl translation -> passing sslmode
     raises TypeError. asyncpg accepts a string for `ssl` and parses it through
     SSLMode (disable/allow/prefer/require/verify-ca/verify-full).

  4. default to ssl=require when neither ssl nor sslmode is present
     Neon requires TLS.

  5. percent-encode the password if it contains reserved characters, so
     urlsplit/SQLAlchemy parse it correctly.

Usage:
    printf '%s' 'postgresql://...' | python neon_dsn_to_asyncpg.py            # dry run
    printf '%s' 'postgresql://...' | python neon_dsn_to_asyncpg.py --write    # patch .env
    python neon_dsn_to_asyncpg.py --self-test
"""
from __future__ import annotations

import pathlib
import re
import sys
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

DROP_PARAMS = {"channel_binding"}
SAFE_PW = "~._-"


def transform(dsn: str) -> tuple[str, list[str]]:
    """Return (converted_dsn, list_of_human_readable_changes).

    The authority is parsed manually rather than with urlsplit: urlsplit ends the
    netloc at the first '/', so a raw (unencoded) password containing '/' or '@'
    gets silently mis-split into the path. Splitting on the LAST '@' before the
    query instead handles those passwords correctly.
    """
    dsn = dsn.strip().strip('"').strip("'")
    if not dsn:
        raise ValueError("empty DSN")

    notes: list[str] = []

    # --- scheme ---
    scheme_raw, sep, rest = dsn.partition("://")
    if not sep:
        raise ValueError("DSN is missing '://'")
    if scheme_raw == "postgresql+asyncpg":
        pass
    elif scheme_raw in ("postgresql", "postgres"):
        notes.append(f"scheme {scheme_raw}:// -> postgresql+asyncpg://")
    else:
        raise ValueError(
            f"unexpected scheme {scheme_raw!r}; expected postgresql/postgres"
        )
    scheme = "postgresql+asyncpg"

    # --- split off fragment then query ---
    rest, _, _fragment = rest.partition("#")
    authority_path, _, query_str = rest.partition("?")

    # --- authority: split on the LAST '@' so '/' and '@' in the password survive ---
    last_at = authority_path.rfind("@")
    if last_at == -1:
        raise ValueError("DSN has no user:password@ section")
    userinfo = authority_path[:last_at]
    hostpath = authority_path[last_at + 1:]
    slash = hostpath.find("/")
    hostport = hostpath if slash == -1 else hostpath[:slash]
    path = "" if slash == -1 else hostpath[slash:]
    if not hostport:
        raise ValueError("DSN has no host")

    user, _, pw = userinfo.partition(":")
    if pw:
        # Don't double-encode an already percent-encoded password.
        if re.search(r"%[0-9A-Fa-f]{2}", pw):
            notes.append("password left as-is (already appears percent-encoded)")
        else:
            pw_enc = quote(pw, safe=SAFE_PW)
            if pw_enc != pw:
                notes.append("password percent-encoded (contained reserved characters)")
            pw = pw_enc
    netloc = f"{user}:{pw}@{hostport}" if pw else f"{user}@{hostport}"

    # --- query ---
    q = parse_qsl(query_str, keep_blank_values=True)
    out: list[tuple[str, str]] = []
    have_ssl = False
    for k, v in q:
        kl = k.lower()
        if kl in DROP_PARAMS:
            notes.append(f"dropped {k}={v} (asyncpg would forward it as a server setting -> server rejects connection)")
            continue
        if kl == "sslmode":
            notes.append(f"sslmode={v} -> ssl={v} (SQLAlchemy passes query params as asyncpg.connect kwargs; there is no sslmode kwarg)")
            out.append(("ssl", v))
            have_ssl = True
            continue
        if kl == "ssl":
            out.append(("ssl", v))
            have_ssl = True
            continue
        out.append((k, v))
        notes.append(f"kept {k}={v} (WARNING: unrecognised by asyncpg -> becomes a Postgres server setting; remove it if the server rejects it)")

    if not have_ssl:
        out.append(("ssl", "require"))
        notes.append("added ssl=require (no ssl/sslmode present; Neon requires TLS)")

    q_new = urlencode(out)
    new = f"{scheme}://{netloc}{path}" + (f"?{q_new}" if q_new else "")
    return new, notes


def self_test() -> int:
    cases = [
        (
            "postgresql://neondb_owner:npg_ABC123@ep-cool-name-a1b2c3.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://neondb_owner:npg_ABC123@ep-cool-name-a1b2c3.us-east-2.aws.neon.tech/neondb?ssl=require",
        ),
        (
            "postgresql://u:p@host.neon.tech/db?channel_binding=require&sslmode=require",
            "postgresql+asyncpg://u:p@host.neon.tech/db?ssl=require",
        ),
        (
            "postgres://u:p@host.neon.tech:5432/db",
            "postgresql+asyncpg://u:p@host.neon.tech:5432/db?ssl=require",
        ),
        (
            "postgresql+asyncpg://u:p@host/db?ssl=require",
            "postgresql+asyncpg://u:p@host/db?ssl=require",
        ),
        (  # password with reserved chars must be encoded
            "postgresql://u:p@ss/w%rd@host/db?sslmode=require",
            "postgresql+asyncpg://u:p%40ss%2Fw%25rd@host/db?ssl=require",
        ),
    ]
    ok = True
    for src, want in cases:
        try:
            got, _ = transform(src)
        except Exception as e:  # noqa: BLE001
            print(f"FAIL (raised {e!r})\n  in:   {src}")
            ok = False
            continue
        status = "PASS" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"{status}\n  in:   {src}\n  out:  {got}")
        if got != want:
            print(f"  want: {want}")
    print()
    print("ALL PASS" if ok else "SOME TESTS FAILED")
    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    dsn = sys.stdin.read()
    try:
        new, notes = transform(dsn)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2

    print("Transformations applied:")
    for n in notes:
        print(f"  - {n}")
    if not notes:
        print("  - none needed")

    parts = urlsplit(new)
    host = parts.netloc.rpartition("@")[2]
    print()
    print(f"result: {len(new)} chars, host={host}, path={parts.path}, query={parts.query}")
    print("(full DSN intentionally not printed)")

    if "--write" in sys.argv:
        env_path = pathlib.Path(__file__).resolve().parent / ".env"
        raw = env_path.read_bytes().decode("utf-8")
        nl = "\r\n" if raw.count("\r\n") > (raw.count("\n") - raw.count("\r\n")) else "\n"
        lines = raw.split(nl)
        found = False
        for i, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == "DATABASE_URL" and not line.lstrip().startswith("#"):
                lines[i] = f"DATABASE_URL={new}"
                found = True
        if not found:
            print("ERROR: DATABASE_URL key not found in .env")
            return 1
        env_path.write_text(nl.join(lines), encoding="utf-8", newline="")
        print(f"\nwrote DATABASE_URL into {env_path.name}")
    else:
        print("\n(dry run - pass --write to patch .env)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
