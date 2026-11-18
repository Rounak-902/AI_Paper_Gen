# P1-T7 Verification: Static Review, Judgment Calls, and Frozen-Guard Proof

**Status of this document.** Everything below is a **static review**. No PowerShell, Docker,
PostgreSQL, or Redis is available in the environment that produced it, so
`scripts/verify-p1-t7.ps1` has **not** been executed here. The only real execution evidence
that exists is **run 1** (`scratch/p1-t7-verification/verification.log`, 2026-08-23
14:35:20 +05:30), which ended `FAIL - 24 checks, 3 failures, 3 of 6 phases`. Proofs 2
through 7 have never produced a single line of real output. Nothing in this document should
be read as a claim that the application passed those proofs.

The script itself was authored by a parallel session and is untracked. It has **not** been
edited in place — Part 1 is a patch list to be applied deliberately, not a diff already
landed.

---

## Part 1 — Patch list for `scripts/verify-p1-t7.ps1`

All line numbers refer to the 1480-line file as it stands (64,501 bytes). Ordered by
severity: the first three are why run 1 died, the next five are why a green run would still
not satisfy the brief's "show the real output" requirement, and the last three are
hardening.

### Blockers — the run cannot reach proofs 2-7 without these

**P1 — line 887: `[0]` on a scalar returns a `[char]`, and this aborted run 1.**

```powershell
# CURRENT (line 887)
$argonHash = ($hashResult.Lines | ForEach-Object { "$_".Trim() } | Where-Object { $_ -match '^\$argon2id\$' })[0]

# PATCH
$argonHash = @($hashResult.Lines | ForEach-Object { "$_".Trim() } | Where-Object { $_ -match '^\$argon2id\$' }) |
    Select-Object -First 1
if (-not $argonHash) {
    Add-Failure 'seed hasher produced no $argon2id$ line'
    $script:Aborted = 'seed hasher produced no $argon2id$ line; cannot continue'
    return
}
```

The null guard is not decoration. `Select-Object -First 1` over an empty array yields
`$null`, and line 895 calls `$argonHash.Contains($TestPlaintext)` — on `$null` that throws
"You cannot call a method on a null-valued expression", which is the same abort with a
different message. While in there, make line 895 null-safe on its own terms by switching the
method call to an operator: `($argonHash -notlike "*$TestPlaintext*")`.

A PowerShell pipeline that emits exactly one object unrolls to that object rather than a
one-element array. The one object here is the PHC string, so `[0]` indexed the *string* and
returned `[char]'$'`. That single character is the whole of run 1's damage: the log shows
`computed hash: $` at line 200, both hash assertions failed against a char (log 201-202),
and line 895's `.Contains(...)` then threw
`Method invocation failed because [System.Char] does not contain a method named 'Contains'`,
aborting the `seed` phase and with it every remaining proof. The hash itself was computed
correctly — log line 197 has the real value. This is an instrument bug end to end.

Worth a repo-wide sweep: any other `(… | Where-Object …)[0]` in the file has the same
latent defect and will fire the day its pipeline happens to match once.

**P2 — script lines 779 and 781-788: delete two dead statements that exit the static phase 1.**

```python
# DELETE (script line 779) — assigned, never read
payload_b64 = ... if False else "placeholder"

# DELETE (script lines 781-788) — raises InvalidKeyError, and its output is never used
none_payload = _jwt.encode(
    {"userId": str(test_uid), "role": "coaching_admin", "sid": test_sid, ...},
    "",
    algorithm="HS256",
)
```

PyJWT refuses an empty HMAC key — `jwt.exceptions.InvalidKeyError: HMAC key must not be
empty.` — which is exactly the behaviour the brief anticipated when it said to hand-assemble
the `alg:none` token. The script **already does that correctly** at lines 790-798, building
`alg_none_token = none_header_b64 + "." + none_payload_b64 + "."` from claims decoded out of
a valid token. These two statements are leftovers from an earlier approach; nothing
downstream references either name, and the misleading comment at line 780
(`# Build proper alg:none token`) goes with them. **Keep `import base64` at line 777** — the
real hand-assembly at 792 and 795 needs it. All 20 static assertions *before* this point
passed, including `hash embeds configured params m=65536,t=3,p=4` and
`expired token raises TokenExpired`.

**P3 — line 571: `redis-cli -a` writes a warning that becomes the captured value.**

```powershell
# CURRENT
$redisPing = & docker compose -f $ComposeFile exec -T redis `
    redis-cli -a $RedisPassword ping 2>&1

# PATCH
$redisPing = & docker compose -f $ComposeFile exec -T redis `
    redis-cli --no-auth-warning -a $RedisPassword ping 2>&1
```

Run 1 passed this check only because it used `-match 'PONG'`. The value actually captured
and logged (log lines 38-53) is the full `NativeCommandError` blob — including the script's
own source line — with `PONG` buried at the end. A `-eq` comparison here would have failed
against correct infrastructure, and the log is unreadable at that point.

### Report quality — required for the brief's proofs, not for the run to finish

**P4 — abort if `$A0` is null after login.** If PROOF 2 fails to extract an access token,
every subsequent proof asserts against `$null` and produces failures that describe the
harness, not the app. Add an explicit `Add-Failure` plus `$script:Aborted` immediately after
the login response is parsed, so the run stops where the truth is instead of emitting five
misleading downstream failures.

**P5 — print the full tokens and the full login body.** Several sites truncate to 200
characters. The brief requires "Show token + decoded claims" and "Show the actual stored
hash", and a truncated JWT cannot be independently signature-verified by a reader of the
log. Print `$A0`, `$R0`, `$R1` and the complete login response body in full. These are
signed with the local throwaway test secrets and are safe to log; `Protect-Secret` already
redacts the raw secret *material* (lines 139-149), which is the part that must not appear.

**P6 — add the `org_context_resolved … stub_auth=False` forensic assertion for `$RID.CtxValid`.**
PROOF 4 currently proves the handler body ran. It does not prove the request traversed
`get_org_context` with real (non-stub) authentication. Asserting both markers on the same
request id closes the gap the brief actually asks about — "through the full guard chain".

**P7 — assert `error.ehRef -eq 'EH-15'` on the refresh rejections in PROOFs 6 and 7.**
`service.py` raises `AuthenticationError(..., eh_ref="EH-15", ...)` on all three refresh
failure paths (lines 250, 256, 274). Asserting the code distinguishes "the refresh flow
rejected this" from "some other 401 happened", which a bare status-code check cannot do.
Do **not** assert absence of `ehRef` here — that would be wrong; the access-token
rejections in PROOF 5 are the ones with no `ehRef`, because `deps.py` raises
`_CREDENTIAL_REQUIRED` without one.

**P8 — `Assert-BodyLacks` the plaintext in the login response body.** PROOF 1 checks the
server logs for `Corr3ct-Horse-Verify-7`. The response body is the other obvious leak
surface and is already parsed at that point, so the assertion is nearly free and cannot be
`[VACUOUS]`.

### Hardening — optional, and cheap

**P9 — positive Cookie self-test before PROOF 6.** This is the one remaining way a correct
application could be reported as broken. PS 5.1 `Invoke-WebRequest -Headers @{Cookie=…}` is
unverified in this harness; if the header does not transmit, refresh returns 401 and the log
shows `refresh_rejected reason=no_cookie`, and PROOFs 6 and 7 both fail against working
code. The diagnostic is already available for free: `POST /api/v1/auth/logout` with a
syntactically valid but unknown refresh cookie logs `logout_unknown_token digest_prefix=…`
(`tokens.py:329`), whereas no cookie at all logs `logout_no_cookie_presented`
(`service.py:313`). Both return `revoked: false`, so **only the log line distinguishes
them** — which is precisely what makes it a clean test of cookie transmission. If the
`no_cookie` line appears, switch to a `WebSession`/`CookieContainer` before blaming the app.

**P10 — derive the Argon2 parameters from `settings` instead of hardcoding, in both places.**
The literal `m=65536,t=3,p=4` appears twice: in the embedded Python at lines 719-722
(`"$argon2id$v=19$m=65536,t=3,p=4$" in h`) and again in PowerShell at lines 892-893
(`-match '^\$argon2id\$v=19\$m=65536,t=3,p=4\$'`). If `ARGON_*` is ever retuned, both fail
against a *correct* application. Read the values from `settings`, compare the hash against
them, then separately assert each is at or above the OWASP floor (m=19456, t=2, p=1). That
checks the real invariant — "the stored hash matches the configured cost, and the configured
cost is strong enough" — rather than today's constants.

**P11 — sweep for other `[0]`-on-pipeline occurrences.** Same class as P1; worth ten minutes
now rather than one abort per future run.

### Do not "fix" these — they are correct as written

- **Line 1379, the two separate `-like` filters on one line.** This is deliberate and right.
  The reuse log line interleaves `revoked_descendants=` between `sid=` and
  `action=session_family_revoked` (`tokens.py:222-226`), so a single combined pattern would
  fail against a correct app. Two filters are immune to the ordering.
- **The 9-step live-proof ordering.** The valid-access-token proof must precede the reuse
  step, because reuse revokes the session family and kills the access token minted under
  that `sid`. Reordering for tidiness breaks PROOF 4.
- **`Complete-Phase` (lines 171-178).** Run 1 printed `++ phase complete: static-contract`
  immediately after a failed check inside that phase. I previously logged this as a
  harness bug of the P1-T4 false-PASS class; that was wrong, and I have corrected it.
  `Complete-Phase` is **byte-identical** to `scripts/verify-p1-t6.ps1` lines 189-196, which
  the brief explicitly required ("reuse its helpers verbatim"), and it cannot produce a
  false PASS because the final verdict at line 1466 independently requires
  `$failCount -eq 0`:
  ```powershell
  $isPass = ($failCount -eq 0) -and ($script:Aborted -eq $null) -and ($phasesReached -eq $phasesTotal)
  ```
  Changing it would break comparability with the T5 and T6 runs for no gain. Annotate if
  you like; do not gate.

### Verified sound — four assumptions that could have failed against correct code, and don't

Each of these was checked against the source rather than assumed, because each is a place
where a wrong assumption in the harness would produce a failure report about the
application:

- **Correlation ids survive normalisation.** `request_context.py`'s allowlist is
  `c.isalnum() or c in "-_.:"`, with `MAX_REQUEST_ID_LENGTH = 128`. Every value in the
  `$RID` table (lines 459-475) is lowercase alphanumerics and underscores, max 31 chars, so
  none is silently replaced by a generated id. Forensic correlation is safe.
- **The marker and the request id land on the same physical line.** `logging_config.py`
  installs `JsonFormatter` + `RequestIdLogFilter` on the **root** logger with `force=True`,
  emitting one JSON object per line carrying `requestId`. So the `Count-Both` approach is
  valid, and app logs go to stdout, i.e. into `$ServerOut`.
- **The camelCase assertions are correct.** `diagnostics.py`'s `DiagModel` and
  `auth/schemas.py`'s `AuthRequest`/`AuthResponse` all use
  `ConfigDict(alias_generator=to_camel, populate_by_name=True)`, and FastAPI serialises
  `by_alias=True`. `data.orgId`, `data.role`, `data.accessToken`, `data.expiresIn`,
  `data.activeOrg.orgId`, `data.revoked` are all right.
- **The seed SQL is schema-valid.** Every NOT NULL column the seed INSERTs omit has a
  `server_default` in migration 0002 (`id`, `status`, `org_type`, `region`, `settings`,
  both timestamps, `row_version`), `users.password_hash` is nullable for SSO, and the seed's
  chosen values sit inside every CHECK vocabulary (`ck_users_status`,
  `ck_memberships_role`, `ck_memberships_status`). The seed phase will not fail on schema
  grounds.

### Running it

```
powershell -ExecutionPolicy Bypass -File scripts\verify-p1-t7.ps1
```

Add `-Fresh` to drop and rebuild `papergen` as run 1 did. PASS requires zero failures **and**
`$script:Aborted -eq $null` **and** all six phase markers reached. A truncated log or a
missing phase marker is not a pass, however many individual checks are green.

---

## Part 2 — Judgment calls, ordered most important first

Each entry cites the governing text. Where the doc and the implementation disagree, the
disagreement is stated rather than smoothed over.

**1. Login rate limiting and account lockout are absent, and this is the most
security-relevant gap in the module.** `IMPLEMENTATION_PLAN.md` line 142 names
"login rate-limit + lockout" as a P1-T7 deliverable, and doc 07 §4.1 line 211 requires
"rate limiting and brute-force protection". Neither exists: `config.py:188` defines
`LOGIN_RATE_LIMIT: int = 50` and nothing reads it, `errors.py:190` defines `RateLimitError`
and nothing raises it for login, and `middleware/__init__.py:40` defers the limiter to
"P1-T10 (infra) / later". The module's own manifest (`auth/__init__.py`) states this is
"absent, not partially built" and explains why it matters: Argon2id at 64 MiB costs the
server ~250ms of memory-hard computation per attempt while costing the attacker almost
nothing, so the control protecting the hashes is simultaneously a denial-of-service
amplifier. Declared, not hidden — but it is a named deliverable that did not ship.

**2. Password reset is absent.** `IMPLEMENTATION_PLAN.md` line 142 specifies "password reset
(single-use expiring token)", doc 07 §4.1 line 215 requires the flow, and §4.1 line 230
lists `user.password_reset` among the events the module emits. Both endpoints are missing.
Also declared in `auth/__init__.py`.

**3. `get_current_user` does not resolve the user from the database, and doc 07 §7.5
arguably requires it.** Line 804 reads: "Validates the JWT, **resolves the user**, rejects
expired/invalid/**revoked** tokens (401)". The implementation validates the signature,
checks the `revoked:{sid}` tombstone, and builds `AuthenticatedUser` from the claims alone —
no per-request `users` read. Claims-only is the conventional stateless-JWT reading and it
avoids a database round trip on every request, but a user disabled mid-window keeps a
working access token until it expires. The exposure is bounded at one access-token lifetime
rather than the 7-day refresh window, because `refresh` *does* re-read `users.status`
(`service.py:262`). Reporting it because the doc's wording is stronger than the code.

Note one correction to an earlier reading of mine: "**revoked**" *is* in §7.5 line 804. The
`revoked:{sid}` tombstone check is therefore **doc-mandated, not an addition**.

**4. Two claims were added to a payload the doc specifies exactly.** Doc 07 §7.1 line 750
gives `Payload: { userId, activeOrgId, role, iat, exp }` — no `typ`, no `sid`. `sid` is
effectively forced by the rest of the doc: §7.3 requires revoking an entire session family
and §7.5 requires rejecting revoked tokens, and neither is possible without a family
identifier inside the token. `typ` guards against an access token being presented where a
refresh token belongs. Both are defensible; both are additions to a specified structure, so
they belong in this list.

**5. `orgId` is optional at login, and the response carries no org list — resolving a real
contradiction between the docs and migration 0025.** Doc 07 §7.4 line 785 assumes
`activeOrgId` is always present ("JWT contains activeOrgId … 1. Load user's memberships"),
and doc 08 line 913 has login return an `orgs` array. But migration 0025 protects
`memberships` with `org_id = current_setting('app.org_id')::uuid`, adds no self-read policy,
and no `app.user_id` GUC exists anywhere in the schema or docs — so as `app_user`
(NOBYPASSRLS), "which orgs does this user belong to" is unanswerable at login, before any
org context exists. `service.py` lines 29-45 record the three options considered (add a GUC
and policy, connect as a bypassing role, or have the client name the org) and why the third
won: no RLS policy, no migration, and no bypassing connection were introduced in a task that
owns none of them. Consequence: a caller naming no `orgId` gets a token that authenticates
but does not authorize, and `get_org_context` returns 403 — which P1-T6 already proved.
Doc 08 lines 934-938's `/select-org` re-mints, and that is P1-T8's endpoint.

**6. The Argon2id parameters were chosen, because doc 07 §7.2 gives none.** Line 761 says
only "**Parameters:** Tuned for ~250ms on target hardware". Chosen: m=65536 (64 MiB), t=3,
p=4 — verified in the real hash from run 1, log line 197:
`$argon2id$v=19$m=65536,t=3,p=4$TmkJuXxhhx4u0VjNv+W5wA$/mBift2huV3/lXd8IWR4/dPcGSHpdRs7YLm1u3DE2Aw`.
Every parameter is at or above the OWASP floor of m=19456, t=2, p=1. A genuine gap in the
doc, filled against an external standard.

**7. `JWT_REFRESH_SECRET` is required by config and used by nothing — a contradiction inside
§7.1 itself.** Line 752 defines the refresh token as an "opaque UUID, ~7 days TTL", and an
opaque UUID has nothing to sign. Yet the setting is mandatory and the verified `.env` carries
a real 51-character value. The secret is dead **by design, not by oversight**; the harness
sets it (line 494) purely to satisfy config validation. Flagged rather than resolved, since
resolving it means editing config or the doc.

**8. The reuse-detection *mechanism* was designed; only the *policy* is specified.** §7.3
lines 767-780 do require "If old token is reused after invalidation: -> Reuse detection ->
revoke entire session family", so reuse detection is not an addition. The doc is silent on
how: the consumed-marker, SHA-256 hashing at rest, the atomic `GETDEL` rotation, and whether
family revocation also kills unexpired *access* tokens. It does, via the `revoked:{sid}`
tombstone that §7.5 line 804 independently mandates.

**9. Refresh re-checks account status — an addition, documented as one.** §7.3 describes
rotation purely as a Redis lookup and says nothing about re-validating the user. Taken
literally, disabling an account would leave its holder minting fresh 15-minute access tokens
for the remaining 7 days. `service.py` lines 47-53 and 260-279 re-read `users.status` and,
because rotation has already happened by then, revoke the family rather than leave a live
credential behind. Cost: one primary-key read per refresh.

**10. HS256 and the ~15-minute / ~7-day lifetimes are NOT judgment calls.** Both
`IMPLEMENTATION_PLAN.md` line 142 and doc 07 §7.1 lines 749 and 752 specify them. Listed
here only so this report does not claim credit for decisions the plan already made.

**11. Timing equalisation on the user-not-found branch is an addition.** Doc 07 §20.4
requires "generic error messages that do not reveal whether an email exists", and the
implementation uses one string — `"Invalid email or password."` — for a missing user, a
wrong password, a disabled account, and a suspended one, with the real reason going only to
`log_detail`, which `errors.py` never serialises. The doc says nothing about *timing*, and a
generic message alone would still leak: a missing email answers in under a millisecond while
a wrong password costs ~250ms of Argon2id, which is a trivially measurable account-existence
oracle and completely invisible in a response body. `dummy_verify_async` performs a real
verification against a throwaway hash so both branches cost the same
(`service.py` lines 14-22, 154).

**12. Wrong-org at login answers 403, not 401.** Doc 07 §10.2 lists authorization failures
under 403, and `get_org_context` already answers the same condition the same way.
`service.py` lines 114-117 records the reasoning: the credential *was* valid, so 401 would
tell a caller their password was wrong when it was not. Relatedly, the password is verified
*before* membership is checked (lines 142-145), so a 403-versus-401 comparison cannot be
used to probe whether an email belongs to an org.

**13. The `DEBUG_*` header constants were deliberately retained, and the audit log's
`actor=` field is still forgeable.** `deps.py` removed the entire stub authentication path,
but kept the constants because `middleware/audit.py` still imports them (line 38) and reads
the actor from client-supplied headers (lines 70-71: `actor = header(DEBUG_USER_ID_HEADER)`,
`org = header(DEBUG_ORG_ID_HEADER)`), then logs them at line 86. So authentication is no
longer forgeable but audit attribution is. The module's docstring self-reports this as "the
highest-value follow-up in the module", and I confirmed it in `audit.py`'s code rather than
taking the docstring's word for it. There is also one stale `log_detail` string left behind
in `deps.py`.

---

## Part 3 — The frozen guards were not modified

Confirmed for `get_org_context`, `require_roles`, `get_scope`, and `get_db`, scoped to the
one file that changed:

```
$ git diff --stat HEAD -- backend/app/common/deps.py
 1 file changed, 136 insertions(+), 58 deletions(-)
```

The diff contains exactly three hunks: `@@ -49,32 +49,57 @@` (imports and constants),
`@@ -149,6 +174,13 @@ class RequestScope:` (around `_parse_uuid`), and
`@@ -162,59 +194,105 @@ async def get_current_user(`. The third hunk spans old lines
162-220 and new lines 194-298, which sits **entirely inside** `get_current_user`
(old 161-223, new 193-301), ending three to four lines before `get_org_context` begins. No
hunk touches any of the four guards.

The stronger proof is the offset table. Net line change is 136 − 58 = **+78**:

| Function | HEAD | Worktree | Shift |
|---|---|---|---|
| `get_current_user` | 161 | 193 | +32 |
| `get_org_context` | 224 | 302 | +78 |
| `require_roles` | 309 | 387 | +78 |
| `get_scope` | 355 | 433 | +78 |
| `get_db` | 373 | 451 | +78 |

All four frozen guards shifted by **exactly the net insertion count**, and by the same
amount as each other. Any edit inside one of them — even a net-zero one that changed a line
without changing the count — would have to be balanced by an equal and opposite edit
elsewhere to preserve a constant offset across all four. A single constant shift equal to
+78 is what "only `get_current_user`'s body changed" looks like in arithmetic.
`get_current_user` shifts by +32 rather than +78 because the remaining +46 lands inside it.

What changed inside `get_current_user`: added
`from app.modules.auth.security import InvalidToken, TokenExpired, decode_access_token`,
`from app.modules.auth.tokens import is_session_revoked`,
`_CREDENTIAL_REQUIRED = "Authentication is required."`, and
`BEARER_SCHEME = "bearer"` (lowercase, since RFC 7235 makes the scheme case-insensitive).
Removed: the whole stub path — `stub_auth_refused`,
`raw_user = request.headers.get(DEBUG_USER_ID_HEADER)`, and the
`"…real auth lands in P1-T7."` message. The two deliberately-retained `DEBUG_*` constants
and one stale `log_detail` string are noted in Part 2, item 13.

---

## What still has no evidence

PROOFs 2 through 7 — login success and its decoded claims, login failure, the full guard
chain reaching a handler, rejection of tampered and expired tokens, refresh rotation, and
reuse detection with family revocation — have **never executed**. Run 1 aborted in `seed`,
three phases in. Everything about the application's live behaviour is currently unproven, and
the 24 checks run 1 did record measured the harness and the substrate, not the auth flow.

Substrate facts run 1 *did* establish, and which are reusable: both containers reach
`Healthy`; `alembic upgrade head` runs 0001→0025 clean on a fresh `papergen` with
`alembic_version = 0025`; `ALTER ROLE app_user PASSWORD` succeeds; the route walker resolves
16 leaf routes; and the real `.env` secrets are 45 and 51 characters, distinct, with no
placeholder substrings.

Apply P1 through P3, run the script in Windows PowerShell, and the three-part report can be
written from real output.
