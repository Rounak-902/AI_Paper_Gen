# P1-T9 Review — Edge Middleware Auth Guard & the `papergen_active_org` Cookie

**Date:** 2026-08-19
**Scope:** Review-only assessment of one design decision in the P1-T9 frontend shell. No frontend files were modified.
**Reviewed artifacts:** `frontend/src/middleware.ts`, `frontend/src/lib/cookies.ts`, `frontend/src/lib/mock-auth.ts`, `frontend/src/lib/jwt.ts`, `frontend/src/app/api/auth/*/route.ts`, `frontend/src/providers/session-provider.tsx`, `frontend/src/services/api-client.ts`, `frontend/src/app/(auth)/layout.tsx`, `docs/docs/08_Frontend_Architecture.md`.

---

## Verdict

**The middleware pattern is acceptable as-is. Doc 08 needs a clarifying amendment. One unrelated but more serious issue was found in the mock auth layer that must not survive P1-T7.**

| # | Question | Verdict |
|---|----------|---------|
| 1 | Does doc 08 specify the two-cookie pattern? | **Partially.** It *mandates the check* (§5.4.3) but is **silent on the mechanism** and names only one cookie anywhere in the document. Doc gap → amend. |
| 2 | Is presence-checking `papergen_active_org` a meaningfully weaker signal? | **No — and it is not the auth gate.** The auth gate is the refresh cookie. Forging `papergen_active_org` alone grants nothing. |
| 3 | Is there a cheap fix, or is this standard? | **Standard and correct.** No security fix warranted. The cookie is already membership-validated before being honored. |
| 4 | Does the real security boundary still hold? | **Structurally yes, but currently vacuous** — there is no real backend wired and 9 of 10 protected routes do not exist. Must be re-verified at P1-T7. |

---

## Correction to the review premise

The task described middleware as checking `papergen_active_org` "to decide whether to allow a request through to an authenticated route, or redirect to /login." **That is not what the code does.** `middleware.ts` reads **two** cookies with two different jobs:

| Cookie | Line | Role in middleware | Failure action |
|--------|------|--------------------|----------------|
| `papergen_refresh` | `middleware.ts:22` | The **session gate** — decides authenticated vs. not | redirect `/login?next=…` |
| `papergen_active_org` | `middleware.ts:23` | **Org-context routing only** | redirect `/select-org?next=…` |

The `/login` decision is driven entirely by the refresh cookie (`middleware.ts:30-35`). `papergen_active_org` can only ever route an *already-session-bearing* request to `/select-org` (`middleware.ts:37-42`). This distinction is what makes the answer to Q2 favourable, so it matters.

---

## Q1 — What doc 08 actually says

**Verified by grep, not recall.**

The doc **does** require the org-presence check at the edge. §5.4 "Responsibilities", point 3 (line 740-741):

> 3. **Enforce active-org presence**: an authenticated user with no active org hitting an app route is sent to `/select-org` (§6.5).

The flowchart at lines 750-756 repeats it, and line 761 names the pair explicitly: *"Edge middleware does the cheap, high-value gate (session presence, org presence)."* So the implemented behaviour is doc-mandated, not invented.

**But the doc never specifies how.** A grep for every occurrence of "cookie" in doc 08 returns matches on **exactly one cookie — the refresh cookie**. There is no mention of `papergen_active_org`, no org cookie, no second cookie of any kind, anywhere in the document.

Worse, the doc's own token model makes the mandated check *impossible as written*:

| Doc statement | Line | Consequence for middleware |
|---|---|---|
| Access token is in memory only, "Never in a cookie" | 5792-5796, 1596 | Middleware cannot read it |
| `activeOrgId` is a claim inside the access token, server-minted | 883-896 | Not edge-readable |
| `session` store is **not persisted**; "rehydrated by silent refresh" | 1451 | Not edge-readable |
| Middleware must nonetheless "enforce active-org presence" | 740-741 | **Requires state the doc never provides** |

This is a genuine internal gap in doc 08, not an implementation deviation. Codex had to invent *some* edge-readable carrier to satisfy §5.4.3, and a second httpOnly cookie is a reasonable choice. Note the doc's "never in a cookie" rule (line 5796) applies to the **access token**, not to a bare org identifier — so the implementation does not violate it. An org id is a low-sensitivity identifier, not a credential.

**Action:** amend §5.4 (and cross-reference §6.5) to name `papergen_active_org`, state that it is an httpOnly non-authoritative routing hint, and state explicitly that it is never trusted as authorization.

---

## Q2 — Attack surface of a forged `papergen_active_org`

First, the general point that `httpOnly` does **not** stop forgery: it blocks JavaScript *reads*. It does nothing to stop the user setting the cookie by hand via devtools or `curl`. So "an attacker sets this cookie themselves" is entirely realistic and worth walking through.

**Forging `papergen_active_org` alone, with no refresh cookie:**

Trace `middleware.ts:30` — `isProtected && !hasSession` is evaluated **first** and is still true, because `hasSession` reads only `papergen_refresh`. The request is redirected to `/login`. The org cookie is never reached. **Worst case: nothing. No bypass.**

**Forging it *alongside* a valid session** (i.e. a real user hand-editing it to another org's id): the value is not trusted. `app/api/auth/refresh/route.ts:15-16` passes it into `buildMockSession` as a *requested* org, and `mock-auth.ts:67-70` honours it only if it passes a membership check:

```js
requestedActiveOrgId && orgs.some((org) => org.id === requestedActiveOrgId)
  ? requestedActiveOrgId
  : fallbackActiveOrgId
```

A non-member org id silently falls back. This mirrors doc 08 line 890 (*"API validates membership"*) and §6.6's rule that the server mints org context. The cookie is a **hint that gets validated**, which is the correct shape. Worst case: the user routes themselves to an org they are already a member of — i.e. exactly what the org switcher does legitimately.

### The real finding: `papergen_refresh` is trivially forgeable

The weak cookie is not the one under review. `papergen_refresh` currently holds an **unsigned, unencrypted base64url JSON blob**, and the only validation is "did it parse" (`refresh/route.ts:11`). Demonstrated concretely:

```
forged papergen_refresh = eyJlbWFpbCI6ImF0dGFja2VyQGV2aWwudGVzdCIsImlzc3VlZEF0IjowLCJub25jZSI6IngifQ
decodes to: {"email":"attacker@evil.test","issuedAt":0,"nonce":"x"}
passes the only check (truthy payload)? true
-> granted role: coaching_admin in org_riverside
```

Anyone can mint themselves a `coaching_admin` session by pasting one cookie, because `getMockOrgs` hands `DEMO_ORGS` to any email not containing "single" (`mock-auth.ts:54-56, 10-11`). The stub access token is also `alg: "none"` with signature `frontend-stub` (`mock-auth.ts:106-117`).

This is **acceptable today only because it is explicitly scaffolding** — every route handler carries `// TODO: wire to real backend once P1-T7 auth endpoints exist` — and because there is nothing real behind it (see Q4). It is called out here because it is a far larger hole than the one under review, and it is the thing that must be deleted, not adapted, when P1-T7 lands.

---

## Q3 — Is there a cheap fix?

**This is the standard pattern for this constraint, and it is the right call here.** Coarse presence-based routing at the edge with authoritative verification downstream is exactly what doc 08 prescribes (line 803: *"Edge middleware (coarse) + layout guards (role) + API (enforce)"*; lines 759-763 give the reasoning: middleware has no permission graph without a round-trip). The doc even pre-acknowledges the weak signal at lines 2413-2415: middleware cannot detect an expired or revoked refresh token *"without a round-trip"*, which is why the `(auth)` layout runs a silent refresh anyway.

Options considered and rejected:

| Option | Assessment |
|---|---|
| Sign/HMAC the org cookie | Solves nothing. The org id is not a credential, and it is already membership-validated server-side before use. Adds key management for no gain. |
| Verify the JWT at the edge | The access token is in memory by design and is *never* sent to middleware. There is nothing at the edge to verify. Non-starter. |
| Edge round-trip to validate the session | Explicitly rejected by doc 08 §5.4 on latency grounds. Would put a network hop in front of every navigation. |
| Leave as-is | **Correct.** The check is a UX optimisation that avoids rendering a shell for a request that will fail anyway. |

The one thing genuinely worth doing is the **doc amendment**, not a code change.

---

## Q4 — Does the real boundary hold?

**Structurally, yes.** `services/api-client.ts:160-176` attaches `Authorization: Bearer <token>` from the in-memory store only, via the `getAccessToken` accessor wired in `session-provider.tsx:46`. Nothing derives authorization from a cookie. `credentials: "same-origin"` (line 124). On 401 it runs a single-flight refresh and replays once (lines 129-137, 256-266), matching doc §10.9. Passing middleware yields a rendered shell with `status: 'loading'` and no data; `session-provider.tsx:52-84` bootstraps via `/auth/refresh` and calls `hardLogout` on failure (line 74), which clears the query cache and redirects. So a middleware bypass buys a skeleton, not data.

**But that guarantee is currently vacuous, and this is the honest caveat:**

| Check | Result |
|---|---|
| Real backend base URL configured | **None.** The only `process.env` reference in `src/` is `NODE_ENV` in `cookies.ts:11`. No `NEXT_PUBLIC_*`, no API URL. |
| Protected routes that exist | **1 of 10** — only `/select-org`. `/dashboard`, `/papers`, `/admin`, `/platform`, `/settings`, `/profile`, `/notifications`, `/question-bank`, `/textbooks` are all 404s. |
| Data reachable behind the gate | Fabricated mock only — two hardcoded demo orgs from `mock-auth.ts:10-11`. |
| `/api/*` gated by middleware | No, and correctly so — the matcher excludes `api` (`middleware.ts:71`); handlers validate the cookie themselves. |

So middleware today guards nine routes that do not exist and one page backed entirely by invented data. **No real tenant data can leak because no real tenant data is reachable.** The claim "the boundary holds" is a statement about the *shape* of the code, verified by reading it — it has not been and cannot be exercised against a live backend yet. It must be re-verified when P1-T7 wires real auth.

---

## Recommended follow-ups (not performed in this session)

1. **Amend doc 08 §5.4** to name `papergen_active_org`, define it as an httpOnly, non-authoritative routing hint, and state it is never an authorization input. Cross-reference from §6.5. This closes the doc's own gap between "enforce org presence at the edge" and "org context lives in memory."
2. **At P1-T7, delete `lib/mock-auth.ts` outright** rather than adapting it. Confirm the real opaque refresh token (Redis-backed, per §6.1) replaces the base64 blob, and that `decodeMockRefreshValue` has no callers left.
3. **Re-run this Q4 assessment once real protected pages exist**, since today's "nothing sensitive behind the gate" finding is a property of the scaffold, not of the design.
4. Optional: add a middleware test asserting that a forged `papergen_active_org` with no refresh cookie still redirects to `/login`, to lock in the ordering at `middleware.ts:30` that makes this safe.

---

## Evidence classification

- **Verified by reading the code / grepping the doc:** all cookie names and line references, doc 08's single-cookie coverage, §5.4's org-presence requirement, route existence, absence of a backend URL, api-client bearer-only behaviour.
- **Verified by executing logic:** the `papergen_refresh` forgery, reproduced by replicating `decodeMockRefreshValue` and `buildMockSession` exactly.
- **Not checkable here:** live end-to-end behaviour against a running Next.js server and the FastAPI backend (backend not wired; Windows `node_modules` not runnable from the review VM).
