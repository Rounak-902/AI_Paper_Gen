# 08 Frontend Architecture

> **Status:** Baseline v1.0 · **Audience:** Frontend engineers, software architects, platform/QA teams
> **Scope:** The web client for the AI Question Paper Generation platform — the browser-based
> application that teachers, coaching admins, and super admins use to configure, generate, review,
> approve, and export question papers, and to administer their organizations.
> **Relationship to siblings:** This document is the client-side counterpart to
> [07 Backend Architecture](07_Backend_Architecture.md). It assumes the request lifecycle, guard
> chain, response envelope, event catalogue, and error taxonomy defined there and in
> [02 System Architecture](02_System_Architecture.md). Where a term is defined in a sibling document
> it is reused verbatim rather than re-defined.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Frontend Design Principles](#2-frontend-design-principles)
3. [Technology Stack](#3-technology-stack)
4. [Project Structure](#4-project-structure)
5. [Routing Architecture](#5-routing-architecture)
6. [Authentication Architecture](#6-authentication-architecture)
7. [Authorization Architecture](#7-authorization-architecture)
8. [Layout Architecture](#8-layout-architecture)
9. [State Management Architecture](#9-state-management-architecture)
10. [API Communication Architecture](#10-api-communication-architecture)
11. [Real-Time Communication](#11-real-time-communication)
12. [Feature Module Architecture](#12-feature-module-architecture)
13. [Paper Builder Architecture](#13-paper-builder-architecture)
14. [Component Architecture](#14-component-architecture)
15. [Form Architecture](#15-form-architecture)
16. [Client-Side Validation](#16-client-side-validation)
17. [File Upload Architecture](#17-file-upload-architecture)
18. [Search & Filtering](#18-search--filtering)
19. [Performance Optimization](#19-performance-optimization)
20. [Responsive Design](#20-responsive-design)
21. [Accessibility](#21-accessibility)
22. [Theme System](#22-theme-system)
23. [Notification System](#23-notification-system)
24. [Security](#24-security)
25. [Error Handling](#25-error-handling)
26. [Frontend Monitoring](#26-frontend-monitoring)
27. [Testing Strategy](#27-testing-strategy)
28. [Future Enhancements](#28-future-enhancements)
29. [Sequence Diagrams](#29-sequence-diagrams)
30. [Related Documents](#30-related-documents)

---

## 1. Overview

### 1.1 Purpose

The frontend is the primary human interface to the AI Question Paper Generation platform. Every
capability the backend exposes — curriculum browsing, textbook ingestion, blueprint configuration,
AI-assisted paper generation, review and approval, export, billing, and administration — reaches the
end user through this web client. The frontend's job is to turn a set of REST endpoints, an
event stream, and a permission model into a coherent, fast, role-appropriate product that a teacher
with no technical background can use under time pressure, and that a super admin can use to operate
the platform.

The document explains **what** the frontend is responsible for, **how** it is structured, and — at
every decision point — **why** it is built the way it is. It is written for the engineers who will
build and maintain the client, and for architects who need to reason about how the client behaves
under failure, at scale, and against the security boundary.

### 1.2 Responsibilities

The frontend owns the following concerns end to end:

| # | Responsibility | Description |
|---|----------------|-------------|
| R1 | Presentation | Render every screen for the three roles (teacher, coaching_admin, super_admin) responsively across desktop, tablet, and mobile. |
| R2 | Interaction | Capture user intent through forms, drag-and-drop, and controls; validate it client-side before it reaches the API. |
| R3 | Session & identity | Manage the JWT access/refresh lifecycle, active-organization context, and role, and reflect them in the UI. |
| R4 | Authorization projection | Show only the navigation, actions, and data a user's role permits; never render a control the backend would reject. |
| R5 | Server-state orchestration | Fetch, cache, invalidate, and re-fetch backend data through a single query layer, keeping the UI consistent with the source of truth. |
| R6 | Real-time reflection | Subscribe to job-progress and domain events over SSE and update the UI as long-running work advances. |
| R7 | Resilience | Degrade gracefully on 401/403/404/409/422/429/5xx, network loss, and stream disconnects, with clear recovery paths. |
| R8 | Performance | Meet Core Web Vitals targets on mid-range hardware and constrained networks. |
| R9 | Accessibility | Meet WCAG 2.1 AA so the product is usable with keyboard and assistive technology. |
| R10 | Observability | Emit performance, error, and usage telemetry so client health is measurable in production. |

What the frontend explicitly does **not** own: authoritative business rules, persistence,
AI orchestration, tenant isolation enforcement, and access-control enforcement. Those live in the
backend. The client mirrors them for usability, but treats the server as the single source of truth
and the only place authorization is *enforced* (see §7.1 and §24).

### 1.3 Scope

**In scope:** the Next.js web application, its build and runtime configuration, its state and data
layers, its component system, its client-side security posture, and its observability. Also in scope
are the contracts the client depends on: the REST envelope, the SSE event shapes, the signed-URL
upload handshake, and the JWT claims.

**Out of scope:** backend service internals (see [07 Backend Architecture](07_Backend_Architecture.md)),
AI retrieval and generation internals (see [06 AI Architecture](06_AI_Architecture.md)), data
schema (see [03 Database Design](03_Database_Design.md)), and deployment topology
(see [09 Deployment](09_Deployment.md)). A native mobile app is out of scope for v1 and discussed only
under [Future Enhancements](#28-future-enhancements).

### 1.4 Goals

1. **Role- and tenant-correct by construction.** A user only ever sees and acts within their role
   and their active organization. The UI makes the wrong action impossible to reach, not merely
   rejected after the fact.
2. **Fast where it matters.** The paper-review and question-bank surfaces stay responsive with
   hundreds of items on screen; navigation between owned data is instant from cache.
3. **Honest about asynchronous work.** Generation, OCR, and export are long-running backend jobs;
   the client always shows their real state and never fakes completion.
4. **Resilient under partial failure.** A dropped token, a lost SSE connection, or a 5xx from one
   endpoint never strands the user on a blank or frozen screen.
5. **Maintainable by a growing team.** Feature boundaries are explicit so several engineers can work
   in parallel without colliding, and so a new engineer can locate any behavior quickly.

### 1.5 How the Frontend Fits Into the Overall Architecture

The frontend is one tier in a four-tier system: **client → API → data/AI services → workers**. It is
a stateless, independently deployable artifact (a set of static assets plus a Node server for
server-side rendering) served from the edge/CDN and talking to the backend exclusively over HTTPS.
It holds no durable state of its own; everything durable lives behind the API.

```
                          ┌───────────────────────────────────────────┐
                          │                 Browser                     │
                          │  ┌─────────────────────────────────────┐    │
                          │  │      Next.js App (this document)     │    │
                          │  │  UI · State · Query cache · SSE hub  │    │
                          │  └─────────────────────────────────────┘    │
                          └───────────────┬─────────────────┬───────────┘
                                          │ HTTPS           │ HTTPS (text/event-stream)
                            REST + JWT    │                 │  SSE job/domain events
                                          ▼                 ▼
                          ┌───────────────────────────────────────────┐
                          │          FastAPI API (Backend Arch)        │
                          │  get_current_user → get_org_context →       │
                          │  require_roles → scope → router → service   │
                          └───┬───────────┬───────────┬─────────────┬───┘
                              │           │           │             │
                     ┌────────▼──┐  ┌─────▼─────┐ ┌───▼────┐   ┌────▼─────┐
                     │PostgreSQL │  │  Redis    │ │Object  │   │  Workers │
                     │  (+RLS,   │  │(cache,    │ │Storage │   │ (Celery: │
                     │  pg_trgm) │  │ pub/sub)  │ │(signed │   │ OCR,gen, │
                     │           │  │           │ │ URLs)  │   │ export)  │
                     └───────────┘  └───────────┘ └────────┘   └──────────┘
```

Three integration surfaces define the entire relationship between this client and the rest of the
system:

| Surface | Direction | Transport | Defined in |
|---------|-----------|-----------|------------|
| REST API | client → backend | HTTPS/JSON, `Authorization: Bearer <jwt>` | [07 Backend Architecture](07_Backend_Architecture.md) |
| Event stream | backend → client | HTTPS `text/event-stream` (SSE), fed by Redis pub/sub | §11, [07 Backend Architecture](07_Backend_Architecture.md) |
| Direct upload | client → object storage | HTTPS `PUT` to a signed URL (API never in the data path) | §17, [05 Textbook Ingestion](05_Textbook_Ingestion.md) |

### 1.6 Relationship With Backend, AI, Database, and Workers

- **Backend (API):** a **separate FastAPI (Python) application** — a different process, image, and log
  stream from this Next.js app, reachable only over HTTPS. It enforces the dependency chain
  `get_current_user → get_org_context → require_roles → scope` on every request, returns the standard
  response envelope, and issues signed URLs. The client never assumes it can bypass a guard; it
  designs the UI so that guarded actions are only offered when they will succeed.
- **AI services:** the client never talks to any model or the retrieval pipeline directly. AI work is
  requested through the API (e.g., "generate this paper from this blueprint"), which returns a
  `202 Accepted` with a job handle; progress arrives over SSE. From the client's perspective, AI is
  just another asynchronous job (see §11, §13).
- **Database:** the client has no knowledge of the schema, RLS policies, or `org_id` scoping beyond
  the shape of the JSON it receives. Tenant isolation is enforced by `get_org_context` + PostgreSQL RLS;
  the client's active-org context (§6.5) exists only to *request* the right data, never to *enforce*
  the boundary.
- **Workers:** OCR, generation, and export run as background workers. The client's contact with them
  is indirect: it starts jobs through the API and observes their progress through the events the
  workers emit (`textbook.parsed`, `paper.generated`, `export.ready`, etc.) relayed over SSE.

### 1.7 High-Level Frontend Composition

Internally the client is layered so that each concern has one home and dependencies point in one
direction (UI depends on state depends on data/transport, never the reverse):

```
┌──────────────────────────────────────────────────────────────────────┐
│  Routing & Layout        app/ route tree · middleware · app shell      │
├──────────────────────────────────────────────────────────────────────┤
│  Feature Modules         auth · dashboard · question-bank · textbooks  │
│                          paper-builder · templates · approval ·        │
│                          analytics · billing · settings · notifications│
├──────────────────────────────────────────────────────────────────────┤
│  Component System        ui/ (shadcn) · shared · layout · feature comp.│
├──────────────┬───────────────────────────────┬───────────────────────┤
│  Client State│  Server State                 │  Real-Time             │
│  (Zustand)   │  (TanStack Query)             │  (SSE hub)             │
├──────────────┴───────────────────────────────┴───────────────────────┤
│  Transport               API client · interceptors · auth · uploads   │
└──────────────────────────────────────────────────────────────────────┘
```

Each of these layers is expanded in the sections that follow: routing/layout in §5 and §8, feature
modules in §12–§13, the component system in §14, the state layers in §9, real-time in §11, and
transport in §10 and §17.

## 2. Frontend Design Principles

These principles are the standing rationale behind every later decision. Each is stated, then
justified — the "why" is the point, because a principle without a reason cannot be applied to a case
the document does not explicitly cover.

### 2.1 Role-Aware UI

**Principle.** The interface is a projection of the current user's role. Navigation, actions, and
data surfaces are composed from the role in the JWT (`teacher`, `coaching_admin`, `super_admin`).

**Why.** The platform serves three audiences with sharply different jobs: a teacher builds and
reviews papers; a coaching admin manages users, programs, templates, and billing for one
organization; a super admin operates the platform across organizations. Showing every user every
control would overwhelm teachers, leak the existence of privileged operations, and multiply the
support burden. Rendering by role keeps each experience focused and reduces the attack surface a
curious user can probe. Crucially, role-aware rendering is a *usability and least-privilege*
mechanism, not a *security* mechanism — the backend still enforces authorization on every call
(§7.1).

### 2.2 Tenant-Aware UI

**Principle.** Every screen operates within exactly one active organization. The active org is part
of the session (§6.5), shown in the shell, and attached implicitly to every request via the JWT.

**Why.** The product is multi-tenant: a teacher may belong to more than one coaching organization,
and a super admin operates across all of them. Ambiguity about "which org am I acting in?" is a data
integrity and privacy hazard — a paper created under the wrong org, or another tenant's questions
shown by mistake, is a serious incident. Making the active tenant explicit and singular removes that
ambiguity from the user's mind and from the code.

### 2.3 Reusable Components

**Principle.** UI is built from a layered component library (primitives → shared → feature) with a
single source of truth for each visual pattern.

**Why.** Consistency and velocity. A single `Button`, `DataTable`, or `ConfirmDialog` used everywhere
means a change to spacing, focus ring, or keyboard behavior happens once and propagates. It also
lowers the cost of accessibility: get focus management right in the primitive and every consumer
inherits it. See §14 for the hierarchy.

### 2.4 Responsive Design

**Principle.** Every screen is usable from a 360 px phone to a wide desktop, with layouts that adapt
rather than merely shrink.

**Why.** Teachers work on laptops and increasingly on tablets and phones; admins on desktops. A paper
that can be reviewed on a phone during a commute is a real workflow. Responsiveness is designed in
from the primitives (§20), not retrofitted, because retrofitting responsive behavior onto a
desktop-only component tree is far more expensive than building it in.

### 2.5 Accessibility

**Principle.** The product targets WCAG 2.1 AA: full keyboard operability, correct semantics, managed
focus, and sufficient contrast.

**Why.** Accessibility is both a legal/procurement requirement for education institutions and simply
correct engineering — accessible components are more robust, more testable, and better for everyone
(keyboard power users, screen-reader users, low-vision users). Building on shadcn/ui (Radix
primitives) gives us accessible behavior by default; the principle keeps us from regressing it. See
§21.

### 2.6 Performance First

**Principle.** Performance is a feature with explicit budgets (§19), not an afterthought. We optimize
the paths users hit most: first load, navigation between owned data, and the paper-review surface.

**Why.** Slow tools get abandoned, and this tool is used against deadlines (exam schedules). Perceived
speed also builds trust in an AI product where users are already uncertain. Budgets make performance a
tracked, non-negotiable requirement rather than a "we'll fix it later."

### 2.7 Minimal Client State

**Principle.** The client holds as little state as possible. Anything the server owns is fetched, not
duplicated; only genuinely client-only concerns (UI toggles, in-progress editor buffers) live in
client state.

**Why.** Every piece of duplicated server state is a bug waiting to happen — it drifts, goes stale,
and must be manually kept in sync. Minimizing client state removes whole classes of consistency bugs
and makes reasoning about the UI simpler. This is the foundation of the state model in §9.

### 2.8 Server-Driven Data

**Principle.** The server is the single source of truth. The client renders what the server returns
and re-fetches to converge, rather than computing authoritative values locally.

**Why.** Business rules (quota, approval eligibility, marks totals, permission checks) can change and
must stay authoritative on the server. Duplicating them on the client guarantees divergence over
time. The client may *mirror* a rule for fast feedback (§16), but the server's answer always wins.

### 2.9 Optimistic Updates (Selective)

**Principle.** For safe, reversible, high-frequency actions (reorder a question, toggle a lock, rename
a draft), the UI updates immediately and reconciles with the server response; on failure it rolls
back. For consequential or irreversible actions (approve, export, delete), the UI waits for
confirmation.

**Why.** Optimism makes the editor feel instant where instant matters, but applied to irreversible
actions it would show success that did not happen — unacceptable for approvals and exports. The split
is deliberate (see §9.6 and §13).

### 2.10 Progressive Enhancement & Graceful Degradation

**Principle.** Core reading flows render server-side and work before heavy client JS hydrates.
Real-time is an enhancement: if SSE is unavailable, the client falls back to polling (§11.6) rather
than breaking.

**Why.** Resilience. A feature that only works when every layer is healthy is fragile. Designing the
baseline to work without the enhancement means degraded conditions produce a slower experience, not a
broken one.

### 2.11 Scalability

**Principle.** The frontend architecture scales along three axes: data volume (virtualized lists,
pagination), team size (feature-module boundaries), and feature count (lazy-loaded routes and
code-splitting).

**Why.** The question bank and analytics grow without bound; the engineering team grows; the feature
set grows. Each axis has an explicit mechanism so growth does not degrade performance, developer
velocity, or bundle size. See §4, §18, §19.

### 2.12 Maintainability

**Principle.** Code is organized by feature, with strict layering and one home per concern. Naming and
patterns are consistent across modules.

**Why.** The dominant cost of software is change over time. A new engineer should be able to find the
code for a screen from its URL, and a change to one feature should not ripple into others. Feature
modules with clear public surfaces (§4, §12, §14) make this the default.

### 2.13 Security by Design

**Principle.** The client treats itself as an untrusted, hostile-adjacent environment: it never stores
long-lived secrets in JavaScript-readable storage, sanitizes all rendered rich content, sends the
JWT only to the API origin, and assumes the backend re-checks everything.

**Why.** Anything in the browser can be read or tampered with by the user or by injected script. The
client's security posture is therefore about *reducing blast radius* (short-lived tokens, httpOnly
refresh cookies, CSP, sanitization), never about *enforcing* rules the backend must own. See §24.

### 2.14 Consistency

**Principle.** One design language, one set of tokens, one interaction vocabulary. The same action
looks and behaves the same everywhere; the same error is reported the same way.

**Why.** Consistency is learnability. When "destructive action → confirm dialog → toast on success"
holds everywhere, users build correct expectations and make fewer mistakes, and engineers reach for
the established pattern instead of inventing a new one.

### 2.15 Principle Trade-offs

Principles conflict, and the document is explicit about how they are resolved:

| Tension | Resolution |
|---------|-----------|
| Minimal client state (§2.7) vs. instant, offline-ish editing | The Paper Builder keeps a *deliberate* rich client buffer (§13) — the one sanctioned exception, justified by the editing UX and bounded by autosave. |
| Optimistic updates (§2.9) vs. server-driven truth (§2.8) | Optimism is allowed only for reversible actions; everything consequential waits for the server. |
| Performance-first (§2.6) vs. accessibility (§2.5) | Never traded — a11y is a hard requirement; performance techniques (virtualization) must preserve semantics and keyboard behavior. |
| Role-aware hiding (§2.1) vs. security | Hiding is UX only; enforcement is always server-side (§7.1, §24). |

---

## 3. Technology Stack

### 3.1 Selection Criteria

Every technology below was chosen against four criteria: (1) it solves a concern the product actually
has; (2) it has a large, stable ecosystem and long-term maintenance outlook; (3) it composes cleanly
with the others; and (4) it keeps a clear boundary of responsibility so no two tools fight over the
same job. The recurring theme is **one tool per concern** — routing, styling, primitives, forms,
validation, server state, client state each have exactly one owner.

### 3.2 Stack Summary

| Layer | Technology | Primary Responsibility | Why Chosen (short) |
|-------|-----------|------------------------|--------------------|
| Framework | **Next.js (App Router)** | Routing, SSR/streaming, server components, middleware, build | First-class React, edge middleware for auth gating, streaming for fast first paint |
| Language | **TypeScript** | Static types across UI, state, and API contracts | Compile-time safety on a large multi-engineer codebase; shared types with API DTOs |
| Styling | **Tailwind CSS** | Utility-first styling and design tokens | Consistency via tokens, no dead CSS, fast iteration, small shipped CSS |
| Components | **shadcn/ui** (Radix primitives) | Accessible, ownable component primitives | Accessible-by-default, copied into the repo (no black-box dependency), Tailwind-native |
| Forms | **React Hook Form** | Form state, validation wiring, performance | Uncontrolled inputs → minimal re-renders on large forms (blueprints) |
| Validation | **Zod** | Schema validation, type inference | One schema drives both runtime validation and TS types; mirrors API contracts |
| Server state | **TanStack Query** | Fetching, caching, invalidation, background refetch | Purpose-built for server-state; removes hand-rolled cache bugs |
| Client state | **Zustand** | Global client-only state (session, UI, editor buffer) | Minimal, unopinionated, no boilerplate; scoped stores |
| Auth | **JWT + refresh** | Access token in memory, refresh in httpOnly cookie | Short-lived access, rotating refresh; matches backend model |
| Real-time | **Server-Sent Events** | One-way job/domain event stream | Simpler than WebSockets for server→client-only progress; auto-reconnect |
| Transport | **REST (fetch-based client)** | Backend communication | Matches the FastAPI REST API and envelope |

### 3.3 Framework — Next.js (App Router)

**Responsibility.** File-system routing, the split between Server and Client Components, streaming SSR,
and edge `middleware.ts` that runs before a route renders.

**Why chosen.** Three properties map directly to product needs. (1) **Middleware at the edge** lets us
gate protected routes and normalize the session *before* any page code runs (§5.4, §6). (2) **Server
Components + streaming** let read-heavy pages (dashboards, analytics) send HTML fast and hydrate
progressively, serving §2.6 and §2.10. (3) **App Router layouts** model our nested shell (§8)
naturally — a root layout, an authenticated layout, and role-scoped segment layouts.

**Alternatives considered.** *Plain React SPA (Vite):* simpler, but loses SSR/streaming and edge
middleware, hurting first paint and auth gating. *Remix:* strong data model, smaller ecosystem for our
team's existing skills. *SvelteKit/Nuxt:* excellent, but move us off React, where our component
ecosystem (shadcn/Radix) and hiring pool are strongest. Next.js wins on ecosystem + SSR + middleware.

**Trade-off.** The Server/Client Component split adds conceptual overhead ("where does this run?"). We
accept it and make the boundary explicit in §4 and §14.

### 3.4 Language — TypeScript

**Responsibility.** Types for props, state, hooks, and — most valuably — the API contract (request
DTOs, response envelope, SSE event shapes).

**Why chosen.** On a codebase this size with many contributors, compile-time checks catch the
mismatches that cause the most production incidents: a renamed field, a nullable that wasn't handled,
an enum value the UI forgot. Sharing types with the backend DTOs keeps client and server contracts in
lockstep. Alternative — plain JS with JSDoc — gives weaker guarantees and worse editor support.

**Trade-off.** Type maintenance cost and occasional friction with third-party types; accepted as
clearly net-positive.

### 3.5 Styling — Tailwind CSS

**Responsibility.** All styling, expressed as utilities bound to design tokens (colors, spacing,
typography, radii) defined once (§22).

**Why chosen.** Tailwind makes the design system *enforceable*: components compose from the same token
scale, so spacing and color stay consistent without a separate review step. It ships only the CSS that
is used, keeping the stylesheet small (§19). Alternatives — CSS Modules (more boilerplate, easy drift),
CSS-in-JS (runtime cost, awkward with Server Components) — were rejected primarily for the Server
Component friction and consistency reasons.

**Trade-off.** Verbose class strings; mitigated by extracting components (§14) and a small set of
composed utility patterns.

### 3.6 Components — shadcn/ui

**Responsibility.** The primitive layer: buttons, dialogs, dropdowns, popovers, tabs, tables, toasts —
accessible behaviors built on Radix, styled with Tailwind.

**Why chosen.** shadcn/ui components are **copied into our repository**, not imported from a versioned
package. We own the source, so we can adapt behavior and styling without fighting a dependency, while
inheriting Radix's correct accessibility (focus trapping, ARIA wiring, keyboard interaction). This
directly serves §2.5 and §2.3. Alternative — a full component library (MUI, Chakra) — is faster to
start but harder to restyle to our brand and heavier to ship.

**Trade-off.** We are responsible for updating copied components; accepted in exchange for full
control.

### 3.7 Forms — React Hook Form

**Responsibility.** Form state, submission lifecycle, dirty/touched tracking, and the bridge to Zod
validation (§15).

**Why chosen.** The blueprint and section forms in the Paper Builder are large and frequently edited.
React Hook Form uses uncontrolled inputs, so typing in one field does not re-render the whole form —
essential for performance on big forms (§13, §19). It integrates with Zod through a resolver, so
validation and types come from one schema. Alternative — Formik — re-renders more and has weaker
TypeScript ergonomics.

### 3.8 Validation — Zod

**Responsibility.** Declarative schemas for every form and for parsing API responses at the boundary;
TS types are inferred from the schemas.

**Why chosen.** One Zod schema yields both the runtime check and the static type, eliminating drift
between "what we validate" and "what we typed." Schemas mirror the backend's validation rules (mapped
to the `422` validation errors and EH codes) so client and server agree on what "valid" means (§16).
Alternative — Yup — has weaker type inference; hand-written validators duplicate logic and rot.

### 3.9 Server State — TanStack Query

**Responsibility.** Everything that comes from the API: fetching, caching by key, background refetch,
invalidation after mutations, pagination, and retry policy (§9.4, §10).

**Why chosen.** Server state has hard problems — caching, staleness, deduplication, refetch-on-focus,
invalidation — that teams reliably get wrong by hand. TanStack Query solves them as a library, which
is why the client keeps almost no server data in Zustand. This is the mechanism behind §2.7 and §2.8.
Alternative — Redux Toolkit Query — is capable but pulls in the Redux model we otherwise don't need.

### 3.10 Client State — Zustand

**Responsibility.** The small amount of genuinely client-only global state: the session/active-org
snapshot (§6), UI state (theme, sidebar), and the Paper Builder's editor buffer (§13).

**Why chosen.** Zustand is a minimal store with no provider boilerplate and no action/reducer
ceremony, which fits our principle of *minimal* client state (§2.7). Stores are small and scoped, so
they don't become a dumping ground. Alternative — Redux — is far more boilerplate than our tiny client
footprint justifies; React Context alone re-renders too broadly for the editor buffer.

### 3.11 Authentication — JWT + Refresh

**Responsibility.** Prove identity and active-org on every request via a short-lived JWT access token,
kept fresh by a rotating refresh token.

**Why chosen.** This matches the backend model exactly (§6, [07 Backend Architecture](07_Backend_Architecture.md)):
~15-minute access JWT carrying `{ userId, activeOrgId, role }`, opaque refresh token (~7 days) stored
server-side with rotation and reuse detection. The client keeps the access token in memory and the
refresh token in an httpOnly cookie it cannot read — minimizing token theft blast radius (§24).

### 3.12 Real-Time — Server-Sent Events

**Responsibility.** Deliver backend→client progress and domain events (OCR, generation, export,
approval, notifications) as they happen (§11).

**Why chosen.** Our real-time need is strictly one-directional: the server tells the client that a job
advanced. SSE is the simplest transport for that — it's plain HTTP, works through proxies, carries the
JWT like any request, and reconnects automatically with `Last-Event-ID`. WebSockets add bidirectional
complexity and a separate connection lifecycle we don't need. The pub/sub → API → SSE path is defined
in the backend doc; the client just consumes it.

**Trade-off.** SSE is one-way and has per-connection limits over HTTP/1.1; addressed by a single
multiplexed connection (§11.3) and HTTP/2.

### 3.13 Transport — REST

**Responsibility.** All request/response communication with the API, wrapped in one typed client with
interceptors for auth, retries, and error mapping (§10).

**Why chosen.** The backend is REST with a fixed envelope; a thin typed fetch client matches it
directly, is trivially cacheable by TanStack Query, and keeps the contract obvious. GraphQL was not
chosen because the backend does not expose it and our read patterns are well-served by
purpose-built REST endpoints with server-side pagination/filtering.

### 3.14 Supporting Libraries

| Concern | Library | Note |
|---------|---------|------|
| Drag & drop | dnd-kit | Accessible DnD for section/question reordering (§13); keyboard-operable |
| List virtualization | TanStack Virtual | Renders only visible rows for the question bank and long papers (§19) |
| Tables | TanStack Table | Headless table model for admin/analytics grids |
| Charts | Recharts | Analytics visualizations (§12.9), tree-shakeable |
| Dates | date-fns | Immutable, tree-shakeable date utilities |
| Rich text sanitization | DOMPurify | Sanitize any HTML/rich question content before render (§24.2) |
| Icons | lucide-react | Consistent icon set matching shadcn/ui |
| Telemetry | web-vitals + error reporter | Core Web Vitals + crash reporting (§26) |

Each supporting library is justified by a single concern and is tree-shakeable so it costs bundle size
only where used.

---

## 4. Project Structure

### 4.1 Organizing Principle

The tree is organized **by feature, layered by concern**. Two rules govern it: (1) code that changes
together lives together — a feature owns its components, hooks, schemas, and API calls; (2)
dependencies point downward only — UI depends on state depends on transport, never the reverse, and
features never import each other's internals (they compose through shared surfaces). This is what makes
the codebase scale to many engineers (§2.11, §2.12): a feature is a unit of ownership with a small
public surface.

### 4.2 Top-Level Layout

```
src/
├── app/                     # Next.js App Router: routes, layouts, middleware target
│   ├── (public)/            #   unauthenticated route group (login, forgot-password)
│   ├── (auth)/              #   authenticated route group (app shell layout)
│   │   ├── (teacher)/       #     teacher-scoped segments
│   │   ├── (admin)/         #     coaching_admin-scoped segments
│   │   └── (super-admin)/   #     super_admin-scoped segments
│   ├── api/                 #   route handlers (BFF helpers: token refresh proxy, SSE proxy)
│   ├── layout.tsx           #   root layout (html, providers)
│   └── global-error.tsx     #   top-level error boundary
├── components/              # Presentational component library
│   ├── ui/                  #   shadcn/ui primitives (owned source)
│   ├── shared/              #   composed shared components (DataTable, ConfirmDialog, EmptyState)
│   └── layout/              #   app shell parts (Sidebar, Header, Breadcrumb, OrgSwitcher)
├── features/                # Feature modules (the heart of the app)
│   ├── auth/
│   ├── dashboard/
│   ├── question-bank/
│   ├── textbooks/
│   ├── paper-builder/
│   ├── templates/
│   ├── approval/
│   ├── analytics/
│   ├── billing/
│   ├── settings/
│   ├── notifications/
│   └── profile/
├── hooks/                   # Cross-cutting reusable hooks (useDebounce, useMediaQuery, useSSE)
├── services/                # Transport layer: API client, endpoint modules, upload client
├── stores/                  # Zustand stores (session, ui, paper-builder buffer)
├── providers/               # React context providers (Query, Theme, Session, SSE, Toast)
├── lib/                     # Framework-agnostic helpers (env, jwt-decode, http, constants)
├── styles/                  # Tailwind base, tokens, globals
├── types/                   # Shared TypeScript types (API DTOs, envelope, SSE events, domain)
├── utils/                   # Pure utilities (formatters, marks math, sort/filter helpers)
└── middleware.ts            # Edge middleware: auth gate + route normalization
```

### 4.3 Responsibility of Each Directory

| Directory | Owns | Must NOT contain | Why it exists |
|-----------|------|------------------|---------------|
| `app/` | Route tree, layouts, loading/error boundaries, route handlers | Business logic, reusable components | Next.js maps URLs to files here; keeping it thin (composition only) keeps routing legible (§5) |
| `app/api/` | BFF route handlers: refresh-token proxy, SSE proxy, any same-origin shim | Domain logic | Lets the browser talk same-origin for cookie-bound flows (§6.4) and stream proxying |
| `components/ui/` | Owned shadcn primitives | Feature logic, data fetching | Single source of accessible primitives (§3.6, §14.2) |
| `components/shared/` | App-wide composed components (tables, dialogs, empty/error states) | Feature-specific logic | Reuse across features without a feature owning them (§14.3) |
| `components/layout/` | Shell chrome: sidebar, header, breadcrumb, switchers | Route logic | The app shell (§8) assembled from parts |
| `features/<name>/` | A feature's components, hooks, `api.ts`, `schema.ts`, `types.ts`, `store` (if any) | Another feature's internals | Feature = unit of ownership and code-splitting (§2.11) |
| `hooks/` | Cross-cutting hooks used by ≥2 features | Feature-specific hooks (those live in the feature) | Share truly generic behavior only |
| `services/` | The single API client, interceptors, endpoint wrappers, upload client | React/UI code | One transport home (§10, §17); keeps fetch details out of components |
| `stores/` | Zustand stores for client-only global state | Server data (that's TanStack Query) | Enforces minimal client state (§2.7, §9) |
| `providers/` | Context providers wired in the root/auth layouts | Business logic | One place to compose app-wide context (§9.2) |
| `lib/` | Framework-agnostic helpers and constants | React components | Pure, testable helpers shared everywhere |
| `styles/` | Tailwind config surface, tokens, base CSS | Component styles (those are utilities) | Design tokens live once (§22) |
| `types/` | Shared contracts: API DTOs, envelope, SSE events, domain enums | Runtime code | Client/backend contract in one place (§3.4) |
| `utils/` | Pure functions (formatters, marks math) | Side effects, fetching | Deterministic, unit-tested helpers |
| `middleware.ts` | Edge auth gate and redirects | Rendering, data fetching | Runs before pages; the first authorization checkpoint (§5.4, §6.6) |

### 4.4 Anatomy of a Feature Module

Every feature follows the same internal shape, so any engineer can navigate an unfamiliar feature
instantly:

```
features/paper-builder/
├── components/          # Feature-only components (BlueprintForm, SectionCard, QuestionCard…)
├── hooks/               # Feature hooks (usePaperDraft, useRegenerateQuestion)
├── api.ts               # Endpoint wrappers for this feature (typed, thin over services/apiClient)
├── schema.ts            # Zod schemas for this feature's forms and API parsing
├── types.ts             # Feature-local types (extends shared types/ where relevant)
├── store.ts             # (optional) Zustand store for feature-local client state (editor buffer)
└── index.ts             # Public surface: what other parts of the app may import
```

**The `index.ts` barrel is the contract.** Other code imports `features/paper-builder` only through
its `index.ts`; reaching into `features/paper-builder/components/internal/*` from another feature is a
lint-enforced violation. This is how feature boundaries stay real over time (§2.12).

### 4.5 Import Direction & Boundaries

```
   app/  ──────────────▶  features/  ──────────▶  services/ ─▶ (network)
     │                       │   │                    ▲
     │                       │   └────▶ stores/        │
     ▼                       ▼                         │
 components/layout ─▶ components/shared ─▶ components/ui
                                 │                     │
                                 └────▶ hooks/ ─▶ lib/ / utils/ / types/
```

Rules enforced by lint (`eslint-plugin-boundaries` or equivalent):

- `features/*` may import `components/*`, `hooks/*`, `services/*`, `stores/*`, `lib/*`, `utils/*`,
  `types/*` — but **not** another `features/*` internal.
- `components/*` may import `components/ui/*`, `hooks/*`, `lib/*`, `utils/*`, `types/*` — but **not**
  `features/*` or `services/*` (presentational components don't fetch).
- `app/*` composes features and layouts; it stays thin.
- Nothing imports `app/*`.

**Why these boundaries.** They prevent the two failure modes that kill large frontends: (1) tangled
cross-feature imports that make any change risky, and (2) data fetching leaking into presentational
components, which destroys reusability and testability. The rules turn architectural intent into a CI
check.

---

## 5. Routing Architecture

### 5.1 Model

Routing uses the Next.js App Router: folders under `app/` are URL segments, `layout.tsx` files nest to
form the shell, and **route groups** `(name)` organize routes without adding a URL segment. We use
route groups to express three orthogonal facts about a route — *is it public or authenticated*, and
*which role owns it* — without polluting the URL.

### 5.2 Route Categories

| Category | Location | Auth | Example URLs | Notes |
|----------|----------|------|--------------|-------|
| Public | `app/(public)/` | none | `/login`, `/forgot-password`, `/accept-invite/[token]` | Rendered without a session; redirect to app if already authed |
| Org selection | `app/(auth)/select-org/` | authed, no active org | `/select-org` | Interstitial for multi-org users (§6.5) |
| Authenticated (shared) | `app/(auth)/` | authed | `/dashboard`, `/notifications`, `/profile`, `/settings` | Available to all roles; content adapts by role |
| Teacher | `app/(auth)/(teacher)/` | role: teacher (+admins) | `/papers`, `/papers/[paperId]/build`, `/question-bank`, `/textbooks` | Core authoring surfaces |
| Admin | `app/(auth)/(admin)/` | role: coaching_admin | `/admin/users`, `/admin/programs`, `/admin/templates`, `/admin/billing`, `/admin/approvals` | Org administration |
| Super admin | `app/(auth)/(super-admin)/` | role: super_admin | `/platform/orgs`, `/platform/analytics`, `/platform/prompt-versions`, `/platform/audit` | Cross-tenant operation |
| Dynamic | any | inherits | `/papers/[paperId]`, `/question-bank/[questionId]`, `/admin/users/[userId]` | Parameterized resource routes |
| Not-found / error | per segment | inherits | `not-found.tsx`, `error.tsx` per segment | Localized failure UI (§25) |

**Role-in-URL note.** The `(teacher)`/`(admin)`/`(super-admin)` groups do not appear in the URL; they
exist so a *segment layout* can apply the role guard and role-specific chrome to everything beneath it
in one place (§5.5). A coaching_admin can reach teacher surfaces (they can author too); a teacher
cannot reach `/admin/*` (§7).

### 5.3 Route Tree Diagram

```
/
├── (public)
│   ├── /login
│   ├── /forgot-password
│   └── /accept-invite/[token]
└── (auth)                         ← requires valid session (middleware + SessionProvider)
    ├── /select-org                ← if user has >1 org and none active
    ├── /dashboard                 ← role-adaptive landing
    ├── /notifications
    ├── /profile
    ├── /settings
    ├── (teacher)                  ← RolesGate: teacher | coaching_admin
    │   ├── /papers
    │   ├── /papers/[paperId]
    │   ├── /papers/[paperId]/build      ← Paper Builder (§13)
    │   ├── /question-bank
    │   ├── /question-bank/[questionId]
    │   └── /textbooks
    │       └── /textbooks/[textbookId]
    ├── (admin)                    ← RolesGate: coaching_admin
    │   └── /admin
    │       ├── /users
    │       ├── /programs
    │       ├── /templates
    │       ├── /approvals
    │       └── /billing
    └── (super-admin)              ← RolesGate: super_admin
        └── /platform
            ├── /orgs
            ├── /analytics
            ├── /prompt-versions
            └── /audit
```

### 5.4 Edge Middleware (`middleware.ts`)

`middleware.ts` runs at the edge before any route in a protected group renders. It is the **first**
authorization checkpoint and it is intentionally coarse — it answers "is there any plausible session
here, and does this URL require one?" It does not make fine-grained role decisions (those need data
and belong in layouts and the backend).

Responsibilities:

1. **Read the refresh cookie** (httpOnly) and the presence of a session; if a protected route is
   requested with no session, redirect to `/login?next=<path>`.
2. **Bounce authenticated users away from public routes** (e.g., visiting `/login` while logged in →
   `/dashboard`).
3. **Enforce active-org presence**: an authenticated user with no active org hitting an app route is
   sent to `/select-org` (§6.5).
4. **Attach request context** (request id passthrough) and set security headers where applicable.
### 5.4.1 Active-org enforcement at the edge — mechanism clarification

§5.4 point 3 requires middleware to enforce active-org presence, but the
session's `activeOrgId` lives only in the in-memory access-token claim
(§883–896) and the session store is explicitly non-persisted (§1451) — making
it unreadable at the edge by this document's own design. This section
resolves that gap.

**Mechanism:** a second httpOnly cookie, `papergen_active_org`, carries the
active org ID for edge-routing purposes only. Middleware uses it to decide
whether to route an already-session-bearing request to `/select-org` (no
active org chosen yet) versus an authenticated route.

**This cookie is a routing hint, not a credential, and must never be treated
as one.** Its value is re-validated against the user's actual memberships
before being honored anywhere client-side (see the membership check in the
session provider). A forged or stale cookie value can, at worst, route a
request to `/select-org` or to an organization the user is not actually a
member of at the routing layer — it cannot grant access to any org's data,
because every real data-fetching call still requires the verified access
token from memory, per §5796's existing rule that the access token itself
never leaves memory.

**Why not sign or verify it at the edge:** signing this cookie would not add
real security, since it carries no authorization weight on its own — it only
short-circuits a redirect that would otherwise happen anyway once a
client-side membership check runs. Edge-verifying the actual session (e.g.
validating a JWT in middleware) is not attempted here, consistent with
§803's coarse-edge/authoritative-API pattern: middleware performs approximate,
UX-level routing; the API layer remains the sole real authorization boundary.

**Non-negotiable:** no cookie, ever, may carry the access token itself or any
value that a backend endpoint would accept as proof of authorization on its
own. `papergen_active_org` exists purely to avoid an unnecessary
redirect-then-redirect-again UX flicker; removing it entirely would degrade
UX but never create a security gap, since the API layer would simply reject
any request lacking a valid access token regardless of what middleware
decided.
### 5.4.2 Note on the P1-T9 scaffolding auth layer

`frontend/lib/mock-auth.ts`, built during P1-T9 as scaffolding, uses an
unsigned, unverified session blob and exists solely to unblock frontend
development before the real backend auth (P1-T7) is live. **This file must
be deleted entirely when P1-T7 lands — not adapted or incrementally
migrated.** Its structure superficially resembles a real token flow closely
enough that partial adaptation risks carrying forward code with no actual
cryptographic verification into what looks like production auth.

```
Request ──▶ middleware.ts (edge)
              │
              ├─ public route?  ── yes ─▶ authed? ─ yes ─▶ redirect /dashboard
              │                                   └ no  ─▶ render
              │
              └─ protected route?
                     │
                     ├─ session cookie present? ── no ─▶ redirect /login?next=…
                     │
                     ├─ active org set? ── no ─▶ redirect /select-org
                     │
                     └─ yes ─▶ continue to route (layout guards refine by role, §5.5)
```

**Why coarse at the edge.** Middleware has no access to the full user/permission graph without a
round-trip, and running fine-grained checks there would either be slow or duplicate backend logic.
Edge middleware does the cheap, high-value gate (session presence, org presence); role gating happens
in server layouts where the session is already resolved, and *enforcement* always happens at the API
(§7.1). Defense in depth, not duplicated authority.

### 5.5 Route Guards (Layout-Level)

Each role route group has a **segment layout** that acts as a guard component. Because App Router
layouts wrap everything beneath them, a single `(admin)/layout.tsx` guards every admin page:

```
(admin)/layout.tsx
   └─ read session (already resolved by SessionProvider)
      ├─ role === coaching_admin ?  ── yes ─▶ render <AdminShell>{children}</AdminShell>
      └─ no ─▶ render <Forbidden />   (403 UI, §7.6) — no data is fetched
```

Guards render a **403 surface**, they do not silently redirect, so the user understands *why* they
can't proceed (§7.6). The gate is a projection of the same role that the backend's `require_roles` dependency
enforces — if they ever disagree, the backend wins and the client shows the resulting 403 (§25.4).

### 5.6 Nested & Dynamic Routes

- **Nested layouts** compose the shell: `app/layout.tsx` (html + providers) → `(auth)/layout.tsx`
  (app shell: sidebar/header) → role group layout (role guard + role nav) → page. Each level adds one
  concern; none repeats another's.
- **Dynamic segments** (`[paperId]`, `[questionId]`, `[userId]`) are validated: the segment is parsed,
  the resource is fetched by id through TanStack Query, and a not-found response renders the segment's
  `not-found.tsx` (§25.4). The client never trusts a route param as authorization — fetching the
  resource is itself subject to the backend's tenant + role checks, so an id from another org returns
  404/403, not data.

### 5.7 Loading & Streaming

Each segment can define `loading.tsx`, which App Router shows instantly while the server component
streams. Read-heavy pages (dashboard, analytics) use this to paint a skeleton immediately and stream
content as data resolves — the concrete mechanism behind §2.6 and §2.10.

### 5.8 Routing Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Role in URL? | No — via route groups | Clean URLs; role chrome/guards applied once per group | Guards live in layouts, not the path |
| Where to gate auth? | Edge middleware (coarse) + layout guards (role) + API (enforce) | Defense in depth; cheap check early, authoritative check server-side | Two client checkpoints to keep in sync with backend roles |
| Redirect vs. 403 on role mismatch | 403 surface | Honesty — user learns why, can request access | One more page to render |
| Dynamic route trust | Never trust params; fetch + rely on backend scoping | Tenant isolation stays server-side (§24.7) | Extra fetch to resolve/authorize |

---

## 6. Authentication Architecture

### 6.1 Model & Token Strategy

Authentication mirrors the backend contract in [07 Backend Architecture](07_Backend_Architecture.md)
exactly. Two tokens with two very different lifetimes and storage locations:

| Token | Type | Lifetime | Storage | Read by JS? | Purpose |
|-------|------|----------|---------|-------------|---------|
| Access token | JWT, payload `{ userId, activeOrgId, role, iat, exp }` | ~15 min | In-memory (session store, §6.3) | Yes (decoded, not trusted) | Sent as `Authorization: Bearer` on every API/SSE request |
| Refresh token | Opaque UUID, server-side record in Redis | ~7 days | httpOnly, Secure, SameSite cookie | **No** | Obtain a new access token; single-use with rotation |

**Why this split.** The access token is short-lived so a stolen token expires fast; it lives only in
memory so it vanishes on tab close and is never persisted where XSS could scrape it from
`localStorage`. The refresh token is long-lived but **unreadable by JavaScript** (httpOnly), so even a
successful XSS cannot exfiltrate it; it is single-use with rotation and reuse detection, so replay of a
captured refresh token revokes the whole session family server-side. This is the standard "short
memory access + httpOnly rotating refresh" posture, and it is chosen specifically to bound the blast
radius of the browser being a hostile environment (§2.13, §24.3).

### 6.2 Why Not `localStorage` for the Access Token?

`localStorage` is readable by any script on the origin; a single XSS payload can read it and exfiltrate
the token. Memory storage means the token exists only in the running JS heap of the current tab and is
gone on reload — at the cost of needing a silent refresh on load (§6.7). We accept that cost because it
removes the most common token-theft vector. The refresh cookie survives reload (httpOnly), so silent
re-authentication is seamless without ever exposing a long-lived credential to script.

### 6.3 Session Representation on the Client

The client keeps a small, derived **session snapshot** in a Zustand store (`stores/session.ts`):

```
SessionState = {
  status: 'loading' | 'authenticated' | 'unauthenticated',
  accessToken: string | null,          // in memory only
  user: { id, name, email } | null,
  role: 'teacher' | 'coaching_admin' | 'super_admin' | null,
  activeOrgId: string | null,
  orgs: Array<{ id, name, role }>,      // orgs this user belongs to
  expiresAt: number | null,             // from JWT exp, drives proactive refresh
}
```

The store holds *derived* identity, decoded from the JWT for convenience only. It is never treated as
authoritative — the backend re-derives identity from the token signature on every request. The
`SessionProvider` (§9.2) hydrates this store on load via a silent refresh and exposes it through a
`useSession()` hook.

### 6.4 Login Flow

```
User ── credentials ─▶ POST /auth/login  (same-origin route handler proxy, app/api/auth/login)
                          │
                          ▼
                       FastAPI API validates (public login route; rate-limited, EH-... )
                          │
      ◀── 200 { data: { accessToken, user, orgs } }  + Set-Cookie: refresh=…; HttpOnly; Secure; SameSite
                          │
      Client:  store accessToken (memory) + user/orgs (session store)
                          │
      one org?  ── yes ─▶ set activeOrgId ─▶ redirect next|/dashboard
                └─ no  ─▶ redirect /select-org  (§6.5)
```

The browser talks to a **same-origin route handler** (`app/api/auth/login`) rather than the API
directly, so the `Set-Cookie` for the refresh token is first-party and the httpOnly cookie is bound to
our origin. The route handler forwards to the FastAPI API and relays the `Set-Cookie`. This keeps the
refresh cookie out of cross-origin complications and lets us set `SameSite=Strict` (§24.4).

### 6.5 Organization Selection & Active-Org Context

A user may belong to several organizations (a teacher tutoring for two coaching centers; a super admin
who also administers one). The **active org** is the single tenant every request acts within, and it is
encoded in the access token's `activeOrgId` claim — meaning the *server* mints the org context, the
client only requests it.

```
/select-org  (only when orgs.length > 1 and no activeOrgId)
   └─ user picks org
        └─ POST /auth/switch-org { orgId }
             └─ API validates membership ─▶ 200 { accessToken' with activeOrgId=orgId }
                  └─ client replaces access token, sets activeOrgId, invalidates ALL query cache
                       └─ redirect /dashboard
```

**Why re-mint the token on switch.** Because tenant scoping is enforced by the backend from the
`activeOrgId` claim (`get_org_context` + RLS), the only correct way to change tenant is to obtain a new token
carrying the new claim. The client cannot "pretend" to be in another org by changing a variable — it
must ask the server, which validates membership and issues a token. See §8.6 for the org switcher UI.

### 6.6 Tenant Switching (Mid-Session)

Switching org from inside the app (org switcher in the header) uses the same `/auth/switch-org` call,
then performs a **hard cache reset**: the entire TanStack Query cache is cleared and the SSE connection
is re-established with the new token (§11.7). This is essential — every cached list, detail, and
in-flight query was scoped to the previous org and must not bleed across the tenant boundary. See the
tenant-switch sequence diagram in §29.7.

### 6.7 Silent Refresh & Session Lifecycle

The access token is refreshed **proactively** (before expiry) and **reactively** (on a 401), both via
the httpOnly refresh cookie:

```
Proactive:  scheduler wakes ~60s before expiresAt
Reactive:   API returns 401 with EH auth code

        ─▶ POST /auth/refresh   (cookie sent automatically; body empty)
              │
              ├─ 200 { accessToken' }  + Set-Cookie: refresh' (rotated)
              │      └─ store new token; reschedule; retry the original request (reactive path)
              │
              └─ 401 (refresh invalid/expired/reused)
                     └─ hard logout: clear session store, drop token, redirect /login?next=…
```

The reactive refresh is coordinated by the API-client interceptor (§10.4) with a **single-flight**
guard: if many requests 401 at once, only one refresh call is made and the rest await its result, so a
burst of expiries does not stampede the refresh endpoint. Reuse detection is a server concern — if the
server reports refresh reuse, it revokes the session family and the next refresh 401s, forcing
re-login. The client simply obeys.

### 6.8 Logout

```
User ─▶ POST /auth/logout  ──▶ API revokes refresh session (Redis) + clears cookie
   └─ client: clear session store, drop in-memory token, close SSE, clear query cache, redirect /login
```

Logout is best-effort on the network but **unconditional on the client**: even if the logout request
fails, the client still discards all local session state and tokens, so the local session ends
regardless. The server-side revocation ensures the refresh token cannot be reused even if it somehow
persisted.

### 6.9 Login Sequence Diagram

```
 Browser        Route Handler (BFF)      FastAPI API          Redis
   │  submit login    │                      │                  │
   ├─────────────────▶│  POST /auth/login    │                  │
   │                  ├─────────────────────▶│  verify creds    │
   │                  │                      ├─ store refresh ──▶│
   │                  │  200 + Set-Cookie    │◀─────────────────┤
   │                  │◀─────────────────────┤                  │
   │  200 {token,user}│  (relay Set-Cookie)  │                  │
   │◀─────────────────┤                      │                  │
   │  store token(mem)│                      │                  │
   │  open SSE (§11)  │                      │                  │
   │─────────────────────────────────────────────────────────▶ (event stream)
   │  route: /dashboard or /select-org                          │
```

### 6.10 Authentication Failure Paths

| Situation | Client behavior | User-facing result |
|-----------|-----------------|--------------------|
| Wrong credentials | Show inline form error from envelope `error.message` | "Email or password is incorrect" |
| Rate-limited login (429) | Disable submit, show cooldown from `Retry-After` | "Too many attempts, try again in Ns" |
| Access token expired | Silent reactive refresh, retry original request | Invisible to user |
| Refresh expired/invalid | Hard logout → `/login?next=…` | Redirected to login, returns to page after |
| Refresh reuse detected | Server revokes family → next refresh 401 → hard logout | Forced re-login (security event) |
| No active org | Redirect `/select-org` | Org picker |
| Network down during refresh | Retry with backoff (§10.5); if persistent, hard logout | Offline banner (§25.3) then login |

---

## 7. Authorization Architecture

### 7.1 Enforcement vs. Projection — The Central Rule

There are two authorization systems and they are **not** peers:

- **Enforcement** happens on the backend, always, on every request, via the dependency chain
  `get_current_user → get_org_context → require_roles → scope` (deny-by-default). This is the *only* place
  authorization is actually enforced.
- **Projection** happens on the frontend: the UI shows only what the role can do, to make the product
  usable and to avoid dangling actions. This is a *convenience and least-privilege* layer, never a
  security boundary.

**Why state this so bluntly.** The single most dangerous frontend mistake is to treat hidden UI as
security. If the client ever "protects" an operation only by not rendering the button, an attacker who
crafts the request directly wins. Our rule: the client hides for usability; the backend rejects for
security; the two are kept consistent but the backend is authoritative. Every "can I?" the UI answers
is a *prediction* of what the backend will allow, and when the prediction is wrong the backend's 403
is handled gracefully (§7.6, §25.4).

### 7.2 Role & Capability Model (Client Mirror)

The client mirrors the backend RBAC capability matrix so it can render correctly. This mirror is
loaded from the backend (a `/me/capabilities` response) rather than hard-coded, so a backend policy
change does not require a client release:

| Capability | teacher | coaching_admin | super_admin |
|------------|:-------:|:--------------:|:-----------:|
| View / build papers | ✓ | ✓ | ✓ (any org) |
| Generate papers (AI) | ✓ | ✓ | ✓ |
| Approve papers | – | ✓ | ✓ |
| Manage question bank | ✓ (own/org) | ✓ | ✓ |
| Upload textbooks / OCR | ✓ | ✓ | ✓ |
| Manage users | – | ✓ (own org) | ✓ (any org) |
| Manage programs/curriculum | – | ✓ | ✓ |
| Manage templates | – | ✓ | ✓ |
| Manage billing / view quota | – | ✓ | ✓ |
| View org analytics | – | ✓ | ✓ |
| Manage prompt versions | – | – | ✓ |
| View platform analytics / audit | – | – | ✓ |
| Manage organizations | – | – | ✓ |

Note the separation-of-duties rule (BR-6): the author of a paper cannot be its sole approver. This is
enforced server-side; the client mirrors it by disabling the "Approve" action on a paper the current
user authored (§7.5, §12.7).

**These three role values are the complete implemented set** — `teacher`, `coaching_admin`,
`super_admin` ([ADR-011](ADR/ADR-011-three-roles-extendable-enum.md)). `hod`, `reviewer`, and `viewer`
are reserved values that the backend may add later without a schema change.

Because the matrix is loaded from `/me/capabilities` rather than hard-coded, a new role arriving on the
backend does **not** break this client: unknown roles resolve through the capability list they carry,
and `<Can>` gates on capabilities rather than on role equality. The single place that must change is
the route-group gating in §5.5, which maps roles to navigable areas. This is the concrete payoff of
"mirror capabilities, never re-derive them" — and the reason no component should ever compare
`role === 'coaching_admin'` directly (§7.3).

### 7.3 Permission-Based Rendering

Rendering decisions are centralized in a `<Can>` component and a `useCan()` hook that consult the
capability mirror and, where needed, resource-level facts (ownership, paper state):

```
<Can do="paper.approve" on={paper}>
   <ApproveButton paper={paper} />
</Can>
```

`useCan('paper.approve', paper)` returns false when the role lacks the capability *or* when the
resource-level rule fails (e.g., the current user is the paper's author, BR-6). Consumers never
hand-roll `role === 'coaching_admin'` checks scattered through components — that pattern drifts and is
impossible to audit. One capability predicate, used everywhere, keeps projection consistent (§2.14)
and reviewable.

### 7.4 Role-Aware Navigation

The sidebar and command palette are built from a **navigation manifest** filtered by capability. Items
the role cannot use are not rendered at all (not merely disabled), so navigation stays uncluttered and
does not advertise privileged areas:

```
navManifest = [
  { label: 'Dashboard',    href: '/dashboard',        show: always },
  { label: 'Papers',       href: '/papers',           show: can('paper.view') },
  { label: 'Question Bank',href: '/question-bank',     show: can('question.manage') },
  { label: 'Approvals',    href: '/admin/approvals',   show: can('paper.approve') },
  { label: 'Users',        href: '/admin/users',       show: can('user.manage') },
  { label: 'Platform',     href: '/platform/orgs',     show: can('org.manage') },
  … ]
```

See §8.4 for how the shell renders this manifest.

### 7.5 Hidden vs. Disabled UI — When to Use Which

| Pattern | When | Example | Why |
|---------|------|---------|-----|
| **Hidden** | The user's role fundamentally lacks the capability | Teacher never sees `/admin/*` nav or the Approve action | Don't advertise capabilities the user can never have; reduces clutter and probing |
| **Disabled + reason** | The user *has* the capability but the *specific instance* is not permitted right now | coaching_admin sees "Approve" but it's disabled on a paper they authored (BR-6), or on a paper not yet in `pending_approval` | The action is normally theirs; explaining why it's blocked here is better UX than hiding it |

The rule: **hide by role, disable by state.** Disabled controls always carry a tooltip explaining the
reason, so the user is never left guessing (§21.5, §23).

### 7.6 403 Handling & Fallback Pages

Two distinct 403 situations, handled differently:

1. **Navigating to a forbidden route** → the role route-group guard (§5.5) renders a dedicated
   `<Forbidden />` page: a clear "You don't have access to this area" with the user's role, the
   resource, and a link back to their dashboard (and, for admins, a "request access" affordance).
2. **A permitted-looking action returns 403 at the API** (projection was wrong, or state changed
   underneath the user) → the API-client interceptor (§10.6) surfaces a toast mapped from the error
   envelope's `error.code`/`ehRef`, and the relevant query is invalidated so the UI re-syncs with the
   real permission/state. The user is not thrown to a full-page error for an action-level 403.

**Why differentiate.** A whole forbidden *area* deserves a full-page explanation; a single forbidden
*action* should not destroy the user's context — it should correct the UI and explain. Both paths trace
the backend's `require_roles`/scope decision and both reference the same EH error taxonomy so
support can correlate a user report to a server log by `requestId`/`ehRef` (§25, §26.3).

### 7.7 API Authorization from the Client's View

The client never sends a "role" or "permission" field — authority is entirely in the signed JWT. The
client's only authorization responsibilities on the wire are: (1) attach the current access token; (2)
attach nothing that could be spoofed (no client-set `org_id` in bodies for tenant scoping — the server
derives it from the token). If an endpoint needs an org-relative id (e.g., a `programId`), the server
still validates it belongs to the token's `activeOrgId`. This keeps tenant isolation entirely
server-side (§24.7).

### 7.8 Authorization Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Source of capability matrix | Fetched from `/me/capabilities`, not hard-coded | Backend policy changes without a client release; single source of truth | One extra request on session bootstrap (cached) |
| Central predicate | `useCan()` / `<Can>` everywhere | Consistent, auditable projection; no scattered role checks | All contributors must use it (lint/review) |
| Hide vs disable | Hide by role, disable by state | Matches user mental model; avoids advertising unreachable features | Requires judgment per action (documented in §7.5) |
| Action-level 403 | Toast + query invalidation, not full-page | Preserves context; self-heals the UI | Must map every EH code to a message (§25.6) |

---

## 8. Layout Architecture

### 8.1 The App Shell Concept

Every authenticated screen renders inside a persistent **app shell**: a fixed frame of navigation and
context chrome that does not re-render or flicker as the user moves between pages. The shell is what
makes the product feel like an application rather than a series of documents, and it is where the two
facts that govern everything — *who am I* and *which organization am I in* — are permanently visible.

The shell is assembled from App Router layouts, so it is rendered once and preserved across
navigations. The practical consequence is that navigating from `/papers` to `/question-bank` re-renders
only the page slot; the sidebar keeps its scroll position and collapse state, the header keeps its
notification state, and no layout shift occurs.

### 8.2 Layout Nesting

```
app/layout.tsx  (root)
│  <html>, fonts, base CSS, theme class, Providers (Query, Theme, Toast)
│
├── (public)/layout.tsx
│      centered card layout, product mark, no navigation, no session-dependent chrome
│
└── (auth)/layout.tsx                        ← SessionProvider + SSE hub live here
    │  ┌──────────────── AppShell ────────────────┐
    │  │  <Header/>   <Sidebar/>   {children}      │
    │  └───────────────────────────────────────────┘
    │
    ├── (teacher)/layout.tsx     RolesGate(teacher|coaching_admin) + teacher nav section
    │     └── papers/[paperId]/build/layout.tsx   ← FocusedLayout (shell reduced, §8.9)
    ├── (admin)/layout.tsx       RolesGate(coaching_admin) + admin nav section
    └── (super-admin)/layout.tsx RolesGate(super_admin) + platform nav section
```

Each level adds exactly one concern and repeats none of another's:

| Level | Adds | Why here and not elsewhere |
|-------|------|----------------------------|
| Root | Document skeleton, fonts, theme class, app-wide providers | Must wrap everything, including public routes and error pages |
| `(public)` | Minimal unauthenticated frame | Public pages must not depend on session or fetch capabilities |
| `(auth)` | Session resolution, SSE hub, app shell chrome | One place where "logged in" becomes true; the stream and shell should exist once per session, not per page |
| Role group | Role gate + role's navigation section | Guards every descendant page in one file (§5.5); nav composition is role-specific |
| Segment | Page-specific layout variants (e.g. focused builder) | Only the pages that need a different frame pay for it |

### 8.3 Shell Regions

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ HEADER                                                                        │
│ ┌─────────┐ ┌──────────────┐  ┌───────────────────┐   ┌───┐ ┌───┐ ┌────────┐ │
│ │ ☰ / logo│ │ Org switcher │  │ Breadcrumb / title │   │ ⌘ │ │ 🔔│ │ avatar │ │
│ └─────────┘ └──────────────┘  └───────────────────┘   └───┘ └───┘ └────────┘ │
├──────────────────┬────────────────────────────────────────────────────────────┤
│ SIDEBAR          │ CONTENT                                                    │
│                  │ ┌────────────────────────────────────────────────────────┐ │
│ ▸ Dashboard      │ │ PageHeader: title · description · primary actions       │ │
│ ▸ Papers         │ ├────────────────────────────────────────────────────────┤ │
│ ▸ Question Bank  │ │                                                        │ │
│ ▸ Textbooks      │ │   page content (scroll container)                      │ │
│                  │ │                                                        │ │
│ ADMINISTRATION   │ │                                                        │ │
│ ▸ Approvals      │ │                                                        │ │
│ ▸ Users          │ │                                                        │ │
│ ▸ Templates      │ │                                                        │ │
│ ▸ Billing        │ │                                                        │ │
│                  │ └────────────────────────────────────────────────────────┘ │
│ ─────────────    │                                                            │
│ ⚙ Settings       │                                                            │
│ ⏻ Collapse       │                                                            │
├──────────────────┴────────────────────────────────────────────────────────────┤
│ FOOTER   v1.4.2 · production · Status · Support · Docs                        │
└───────────────────────────────────────────────────────────────────────────────┘
                                          ┌──────────────────┐
   overlays (layered above, §8.11):        │  toast stack     │
   dialogs · drawers · popovers · toasts   └──────────────────┘
```

| Region | Contents | Role-awareness | Responsive behaviour |
|--------|----------|----------------|----------------------|
| Header | Org switcher, breadcrumb/title, command palette trigger, notification bell + unread badge, theme switcher (in user menu), user menu | Org switcher hidden for single-org users; notification bell always present | Stays fixed; breadcrumb truncates to current page on small screens; search collapses to an icon |
| Sidebar | Capability-filtered navigation manifest (§7.4), grouped by section, with active-state; collapse toggle; settings entry | Entire groups absent when the role lacks every item in them | Expanded → icon rail → slide-over drawer (§8.10) |
| Content | `PageHeader` + page body; owns the scroll container | Page content itself is role-adaptive | Padding and grid columns scale by breakpoint |
| Footer | App version, environment, status page, support, docs | None | Hidden on mobile to preserve vertical space |
| Overlay layer | Dialogs, drawers, popovers, tooltips, toasts, command palette | None | Dialogs become bottom sheets on mobile (§20) |

**Why the footer is deliberately minimal.** In an authenticated SaaS application, footer real estate
competes with content and users never navigate from it. It carries only what support and on-call need:
the running version and environment (so a bug report identifies the build) and links to status and
support. Everything else belongs in navigation.

### 8.4 Sidebar Architecture

The sidebar renders the navigation manifest described in §7.4 — a declarative list of items with a
capability predicate — grouped into sections (*Work*, *Administration*, *Platform*). Three behaviours
matter architecturally:

1. **Filtering, not disabling.** An item whose capability predicate fails is not rendered (§7.5).
   A group whose every item is filtered out disappears with its heading, so a teacher never sees an
   empty "Administration" header.
2. **Active-state derivation.** The active item is derived from the current pathname by longest-prefix
   match, not by a manually maintained flag. This means a nested route like
   `/papers/[paperId]/build` correctly highlights *Papers* without any per-route wiring.
3. **Collapse state persistence.** Collapsed/expanded is user preference, so it lives in the `ui`
   Zustand store and is persisted to `localStorage` (§9.9). It is client-only, non-sensitive, and
   should survive reload — exactly the profile of data that belongs in web storage.

### 8.5 Header, Breadcrumb, and Page Title

Breadcrumbs are **derived from the route segment hierarchy plus resolved resource names**, not
hand-authored per page. Each segment contributes either a static label (from a route metadata map) or a
dynamic label resolved from the fetched resource (`/papers/abc123` → the paper's title).

```
/papers/abc123/build
  │       │       └── static  → "Build"
  │       └────────── dynamic → paper.title  ("Class X Mathematics – Term 1")
  └────────────────── static  → "Papers"

Home ▸ Papers ▸ Class X Mathematics – Term 1 ▸ Build
```

The dynamic label depends on data that may still be loading. Rather than shifting layout when the name
arrives, the breadcrumb renders a **fixed-width skeleton** for the unresolved segment and swaps in the
text in place. If the resource 404s, the breadcrumb segment renders the raw id and the page renders its
`not-found` state (§25.4) — the breadcrumb never blocks the page.

Because the resource is already in the TanStack Query cache (the page fetched it), the breadcrumb reads
from cache rather than issuing its own request. This is a concrete instance of the pattern in §14.4:
distant components share server truth through the query cache instead of prop drilling.

### 8.6 Context Switching and the Org Switcher

The active organization is the most consequential piece of ambient context in a multi-tenant product,
so the shell treats it as first-class:

- For a **single-org user**, the org name is displayed as static text. There is no switcher, because a
  control that can only do one thing is noise.
- For a **multi-org user**, the switcher lists memberships with the user's role in each. Selecting one
  triggers the `/auth/switch-org` flow (§6.5).
- For a **super admin**, the switcher additionally supports searching all organizations, because
  cross-tenant operation is their job. The UI marks this state distinctly — an unmistakable badge
  indicating "operating inside <Org> as super admin" — so a platform operator is never confused about
  whose data they are looking at.

Switching is **not** a cheap UI toggle. It re-mints the token, clears the entire query cache, and
reconnects SSE (§6.6, §9.7, §11.7). The UI therefore treats it as a deliberate, confirmable transition
with a visible loading state, and it blocks the switch when there is unsaved work, offering to save
first — losing a draft to an accidental org change would be inexcusable.

### 8.7 Theme Switcher

The theme control lives in the user menu rather than the top-level header: it is a preference set
rarely, and top-level header space is reserved for context and actions used constantly. It offers
*Light*, *Dark*, and *System*; the mechanism, persistence, and flash-prevention are covered in §22.6.

### 8.8 Shell Loading and Streaming Composition

The shell renders **immediately** and independently of page data. This is the single most effective
perceived-performance decision in the layout: the user sees navigation, their org, and the page title
in the first paint, while the page body streams in behind a skeleton (§5.7, §19.7).

```
t0  ── shell HTML (server) ──▶  header + sidebar + page skeleton painted
t1  ── page data resolves  ──▶  content streams into the slot
t2  ── client hydration    ──▶  interactivity (nav, switcher, palette)
```

The shell must therefore never depend on a slow query. Session and capabilities are resolved once in
`(auth)/layout.tsx`; the notification badge and any other data-driven chrome render optimistic
placeholders and fill in when available, never blocking the frame.

### 8.9 Layout Variants

| Variant | Used by | What changes | Why |
|---------|---------|--------------|-----|
| Default app | Most authenticated routes | Full shell | Standard navigation and context |
| Focused builder | `/papers/[paperId]/build` | Sidebar collapses to an icon rail by default; header condenses to context + save state + builder actions; footer hidden | The builder is a three-pane workspace (§13.1) that needs horizontal room; chrome competes with the canvas |
| Print / export preview | Paper preview and print view | No shell at all; page-accurate canvas only | The preview must be a faithful representation of the printed paper (§13.13, §22.13) |
| Public / auth | `(public)` routes | Centered card, product mark only | No session exists, so no context chrome is meaningful |
| Error / forbidden | `error.tsx`, `not-found.tsx`, `<Forbidden/>` | Shell retained where a session exists, so the user can navigate away | Stranding a user on a chrome-less error page is a dead end (§25.4) |

Retaining the shell on error pages is a deliberate resilience choice: a failed page should never remove
the user's ability to go somewhere else.

### 8.10 Responsive Shell Behaviour

```
DESKTOP  ≥1280px                    TABLET  768–1279px              MOBILE  <768px
┌──────┬─────────────────┐          ┌──┬────────────────┐            ┌────────────────┐
│HEADER│                 │          │HEADER             │            │ ☰  Org    🔔 ⋯ │
├──────┼─────────────────┤          ├──┼────────────────┤            ├────────────────┤
│ SIDE │                 │          │▸ │                │            │                │
│ BAR  │    CONTENT      │          │▸ │   CONTENT      │            │    CONTENT     │
│ full │                 │          │▸ │                │            │                │
│ nav  │                 │          │icon rail          │            │  (drawer nav   │
│      │                 │          │(labels on hover / │            │   on ☰ tap)    │
├──────┴─────────────────┤          │ tooltip)          │            ├────────────────┤
│ FOOTER                 │          └──┴────────────────┘            │ ⌂  📄  🔍  🔔  │
└────────────────────────┘                                           └────────────────┘
   persistent sidebar                 collapsible icon rail            drawer + bottom bar
```

The transformation preserves three invariants at every size: the active organization is always
visible, the current location is always identifiable, and every navigation destination is reachable
within one interaction. Breakpoints and touch rules are specified in §20.

### 8.11 Layering and Scroll Policy

A single ordered layer scale prevents the classic z-index arms race. Layers are named tokens (§22.11),
not raw numbers scattered through components:

| Layer | Contents | Notes |
|-------|----------|-------|
| base | Page content | — |
| sticky | Page header, table headers, builder toolbars | Sticky within the content scroll container |
| shell | Header, sidebar | Above sticky content so content never overlaps chrome |
| overlay | Drawers, sheets, dropdowns, popovers | Trap focus where modal (§21.5) |
| dialog | Modal dialogs | Above overlays; only one at a time |
| toast | Toast stack | Above everything, never focus-stealing (§23.4) |
| debug | Dev-only diagnostics | Non-production |

**Scroll containment.** The content region owns the scroll, not the document body. This keeps the
header and sidebar fixed without position hacks, makes "scroll to top on navigation" a local
operation, and lets the builder's panes scroll independently. The one exception is the print/preview
variant, which uses document scroll so browser print pagination behaves correctly.

### 8.12 Layout Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Shell persistence | Rendered in `(auth)` layout, preserved across navigation | No flicker, preserved nav scroll/collapse, faster perceived navigation | Shell must never depend on page data |
| Navigation source | Declarative manifest filtered by capability | One place to audit what each role sees; consistent with §7.4 | Manifest must be kept current as routes are added |
| Breadcrumbs | Derived from segments + cached resource names | No per-page wiring; always correct | Needs a metadata map and skeleton handling for async labels |
| Sidebar collapse | Persisted client preference in `localStorage` | Survives reload; costs nothing; non-sensitive | One more persisted key to manage |
| Builder layout | Dedicated focused variant | The workspace needs the horizontal space | A second layout to maintain |
| Error pages | Keep the shell | User can always navigate away | Slightly more markup on failure paths |
| Z-index | Named layer tokens | Ends ad-hoc z-index escalation | Requires discipline to use the tokens |
| Scroll | Content-region scroll container | Fixed chrome without hacks; independent builder panes | Print view needs the documented exception |

---

## 9. State Management Architecture

### 9.1 Six Categories of State

Most frontend state bugs come from putting a piece of state in the wrong place. The architecture
therefore starts by naming the categories precisely, because the category determines the home:

| Category | Definition | Lifetime | Home | Examples |
|----------|-----------|----------|------|----------|
| **Local** | Needed by one component and its immediate children | Component mount | `useState` / `useReducer` in the component | Dropdown open, hovered row, inline-edit toggle |
| **Global client** | Needed across unrelated parts of the tree, not derived from the server | Session or persisted | Zustand store | Session snapshot, sidebar collapse, theme, upload queue |
| **Server** | Owned by the backend; the client holds a *copy* | Cache-controlled | TanStack Query | Papers, questions, users, analytics, quota, notifications |
| **Form** | In-progress user input not yet submitted | Form mount → submit | React Hook Form | Blueprint form, question editor, settings forms |
| **Temporary / ephemeral** | Transient interaction state, discarded quickly | Seconds | Local state or a scoped store | Drag position, toast queue entries, optimistic snapshots |
| **Persistent** | Survives reload by design | Until cleared | `localStorage` (client-only prefs) or the server (real preferences) | Theme, sidebar collapse, recent searches, saved filters (server) |

The distinction that does the most work is **server vs. everything else**. Server state is a cached
copy of remote truth and needs invalidation, staleness, and refetch semantics; client state is owned
locally and needs none of that. Conflating them — storing fetched papers in a Zustand store — is the
single most common architectural mistake in applications of this kind, and it produces stale UI that
no amount of careful coding fixes.

### 9.2 Where Does This State Belong?

```
                        ┌─────────────────────────────────────┐
                        │ Does the backend own this value?    │
                        └───────────────┬─────────────────────┘
                              yes       │        no
                    ┌───────────────────┘        └──────────────────┐
                    ▼                                              ▼
        ┌───────────────────────┐                  ┌───────────────────────────────┐
        │ TanStack Query        │                  │ Is it in-progress user input   │
        │ (never duplicate it   │                  │ in a form?                     │
        │  into another store)  │                  └────────┬──────────────────┬────┘
        └───────────────────────┘                     yes   │              no  │
                                                            ▼                  ▼
                                              ┌──────────────────────┐  ┌──────────────────────────┐
                                              │ React Hook Form      │  │ Is it needed outside this │
                                              │ (+ Zod schema)       │  │ component's subtree?      │
                                              └──────────────────────┘  └──────┬───────────────┬───┘
                                                                          no   │           yes │
                                                                               ▼               ▼
                                                                    ┌────────────────┐ ┌──────────────────┐
                                                                    │ useState /     │ │ Should it survive │
                                                                    │ useReducer     │ │ a reload?         │
                                                                    └────────────────┘ └───┬──────────┬───┘
                                                                                      no   │      yes │
                                                                                           ▼          ▼
                                                                                  ┌────────────┐ ┌──────────────────┐
                                                                                  │ Zustand    │ │ Zustand +        │
                                                                                  │ store      │ │ persist  ── or ──│
                                                                                  │            │ │ server preference│
                                                                                  └────────────┘ └──────────────────┘
```

The final branch has a rule attached: **persist to the server when the preference should follow the
user across devices** (saved filters, default org, display preferences), and to `localStorage` only
when it is device-specific and trivial (theme, sidebar collapse). Never persist server data or tokens
(§6.2, §9.9, §24.3).

### 9.3 State Ownership Map

```
┌───────────────────────────────────────────────────────────────────────────┐
│                             Providers (root / auth layout)                 │
│  QueryClientProvider · ThemeProvider · SessionProvider · SSEProvider · Toast│
└───────┬──────────────────────────────┬────────────────────────┬───────────┘
        │                              │                        │
        ▼                              ▼                        ▼
┌───────────────────┐   ┌───────────────────────────┐  ┌────────────────────┐
│  Zustand stores   │   │   TanStack Query cache     │  │   SSE hub          │
│  ───────────────  │   │   ──────────────────────   │  │   ────────         │
│  session          │   │  key: [org, entity, …]     │  │  subscribe by      │
│  ui               │◀──│  all server data           │◀─│  event type + id;  │
│  paper-builder    │   │  pagination · staleness    │  │  patch/invalidate  │
│  uploads          │   │  invalidation              │  │  the cache (§9.6)  │
│  toasts           │   └───────────────────────────┘  └────────────────────┘
└───────┬───────────┘                  ▲
        │                              │ read via query hooks
        ▼                              │
┌───────────────────────────────────────────────────────────────────────────┐
│  Components:  local useState  ·  React Hook Form (form state)             │
└───────────────────────────────────────────────────────────────────────────┘
```

Arrows show *who writes to whom*. Note what is absent: no arrow from the query cache into Zustand.
Server data is never copied into a client store. The only permitted direction is the reverse — a store
may hold an id or a filter that *composes a query key*.

### 9.4 Zustand Store Inventory

| Store | Owns | Must never hold | Persisted? |
|-------|------|-----------------|-----------|
| `session` | Auth status, in-memory access token, decoded user/role/activeOrgId, org membership list, token expiry (§6.3) | Server entities; the refresh token (httpOnly cookie); passwords | No — rehydrated by silent refresh |
| `ui` | Sidebar collapse, theme choice, density, last-visited context, command-palette open state | Anything server-owned | Yes (`localStorage`, non-sensitive keys only) |
| `paper-builder` | The editor buffer: working document model, dirty flags, selection, undo/redo stacks, autosave status, lock states (§13.2) | Canonical paper data (that is server state); other papers | Session-scoped draft recovery only (§13.16) |
| `uploads` | Upload queue: files, per-file phase and progress, retry counts, abort handles (§17.9) | File bytes beyond the active transfer | No |
| `toasts` | Transient toast queue and dismissal state (§23.4) | Notification history (that is server state) | No |

Five small stores, each with a stated exclusion list. The exclusions are the important part: they are
what stops a store from becoming a junk drawer, which is the failure mode that makes global state
unmaintainable.

### 9.5 TanStack Query as the Sole Owner of Server State

**Query key conventions.** Every key is hierarchical and **org-scoped as its first element**, without
exception:

```
[orgId, 'papers', { page, pageSize, filters, sort }]     list
[orgId, 'papers', paperId]                                detail
[orgId, 'papers', paperId, 'versions']                    sub-resource
[orgId, 'questions', { q, filters, sort, cursor }]        search
[orgId, 'analytics', 'usage', { from, to, granularity }]  rollup
[orgId, 'quota']                                          singleton
['me']                                                    identity (org-independent)
```

Org-first keys are a **security-relevant** design choice, not just tidiness: they make it structurally
impossible for a cache read in one org to return an entry populated in another, and they make the hard
reset on org switch trivially correct (§9.7, §24.7). Filter objects are normalized (stable key order,
omitted empties) before entering a key, so semantically identical queries share one cache entry.

**Staleness policy by data class.** One global default would be wrong for every class, so staleness is
assigned per class with an explicit reason:

| Data class | `staleTime` | `gcTime` | Refetch on focus | Reason |
|-----------|-------------|----------|------------------|--------|
| Reference / curriculum (boards, subjects, chapter trees) | Hours | Day | No | Changes rarely; large payload; refetching wastes bandwidth |
| Capability mirror (`/me/capabilities`) | Session | Session | No | Changes only on role change, which re-mints the token anyway |
| Entity lists (papers, questions, users) | 30–60 s | 5–10 min | Yes | Others in the org mutate these; focus refetch catches changes cheaply |
| Entity detail | 30–60 s | 10 min | Yes | Same, plus SSE patches keep hot records fresh (§9.6) |
| Search results | 15–30 s | 5 min | No | Highly parameterized; refetching every focus is wasteful and jarring mid-browse |
| Analytics rollups | 15–30 min | 1 h | No | Backed by daily rollups; sub-minute freshness is meaningless |
| Quota / usage | 30 s | 5 min | Yes | Must be current before the user starts expensive AI work (§12.9) |
| Notifications | Real-time via SSE; short fallback | 5 min | Yes | Stream is primary; short staleness covers stream gaps |
| Job status | Real-time via SSE; poll only in fallback | Job lifetime | No | Progress arrives by event; polling is the degraded path (§11.14) |

`refetchOnReconnect` is enabled globally, because a reconnect is exactly the moment cached data is most
likely to be stale (§25.7).

### 9.6 Cache Invalidation and SSE Synchronization

**Invalidation after mutations.** Every mutation declares the keys it invalidates. This is written as a
matrix so it can be reviewed rather than rediscovered:

| Mutation | Invalidates | Why |
|----------|-------------|-----|
| Create / update / delete paper | `[org,'papers']` (list), `[org,'papers',id]` | List membership and the record both change |
| Submit paper for approval | `[org,'papers',id]`, `[org,'approvals']`, `[org,'papers']` | Paper state changes; queue depth changes |
| Approve / reject paper | `[org,'papers',id]`, `[org,'approvals']`, `[org,'analytics','approval-turnaround']` | State, queue, and derived metric |
| Create / edit question | `[org,'questions']`, `[org,'questions',id]` | Bank membership and record |
| Complete textbook upload | `[org,'textbooks']`, `[org,'textbooks',id]` | New record enters the list, then progresses by event |
| Generate paper (202 accepted) | Nothing immediately; the job's events drive updates | Invalidating before the job completes would only refetch unchanged data |
| Create / update template | `[org,'templates']`, `[org,'templates',id]` | — |
| Invite / update / deactivate user | `[org,'users']`, `[org,'users',id]` | — |
| Change plan | `[org,'quota']`, `[org,'billing']` | Limits and billing state both move |
| Switch org | **everything** (cache reset) | Prior entries belong to another tenant (§9.7) |

**SSE-driven updates: patch or invalidate?** Events arrive continuously (§11), and the client must
decide whether to write the payload into the cache or merely mark data stale. The rule:

- **Patch** when the event payload is *authoritative and complete* for the field it touches — job
  progress percentages, a status transition, a completed question object. Patching avoids a network
  round-trip and updates the UI in the same frame as the event.
- **Invalidate** when the event is a *signal* that something changed but does not carry the full new
  shape — "a paper in your org was approved", "quota recalculated", "ingestion produced N questions".
  Invalidation costs a request but guarantees the client converges on server truth.

The bias is toward invalidation, because a wrong patch produces UI that disagrees with the server and
is very hard to notice. Patching is used where the payload is designed for it — which is why the event
catalogue (§11.5) specifies, per event, whether its payload is patch-grade.

High-frequency progress events are coalesced before they touch the cache, so a job emitting many
updates per second causes bounded re-render work (§11.13).

### 9.7 Tenant-Scoped Cache Partitioning and the Hard Reset

Because every key is org-first, the cache is *partitioned* by tenant by construction. On org switch the
client nevertheless performs a **full reset** rather than relying on partitioning alone:

```
switch-org accepted
   │
   ├─ 1. cancel all in-flight queries and mutations   ← prevents a response scoped to the OLD org
   │                                                    landing in the NEW org's cache
   ├─ 2. clear the entire query cache
   ├─ 3. reset feature stores (paper-builder buffer, uploads) after the unsaved-work check (§8.6)
   ├─ 4. replace the access token; update session.activeOrgId
   ├─ 5. reconnect SSE with the new token (§11.7)
   └─ 6. navigate to the dashboard
```

Step 1 is the subtle one and the reason partitioning is insufficient on its own: a request issued
before the switch completes carries the old token and would resolve *after* the new org is active. It
is cancelled rather than allowed to settle. Steps 2 and 3 are defence in depth — even though a stale
entry could not be *read* under the new org's keys, keeping another tenant's data resident in memory is
an unnecessary risk (§24.7).

### 9.8 Optimistic Updates

```
user action
   │
   ├─ 1. snapshot current cache entry
   ├─ 2. write the predicted next state into the cache      → UI updates instantly
   ├─ 3. fire the mutation
   │        ├─ success → invalidate/settle with server truth   (server value wins)
   │        └─ failure → restore the snapshot + explain        (toast, §23.5)
   └─ 4. always re-settle from the server, never leave the predicted value as final
```

Step 4 is the discipline that keeps optimism honest: the predicted value is a placeholder, never the
final truth.

| Optimistic — allowed | Not optimistic — must await the server |
|----------------------|----------------------------------------|
| Reorder sections / questions (§13.6) | Generate or regenerate (async job; §13.8) |
| Toggle a question lock | Submit for approval |
| Rename a draft paper | Approve / reject |
| Mark a notification read | Export / download |
| Toggle a saved filter | Delete anything |
| Add/remove a tag | Plan or billing changes |
| Edit a question field inside the builder buffer | Invite or deactivate a user |

The dividing line is **reversibility and consequence**, exactly as stated in §2.9. If a wrongly-shown
success could mislead a user into acting on work that does not exist — an approval that never
happened, an export that was never produced — optimism is prohibited.

### 9.9 Persistence Policy

| Data | Where | Why there | Why not elsewhere |
|------|-------|-----------|-------------------|
| Theme choice | `localStorage` | Device-specific; needed before first paint to avoid a flash (§22.6) | Server round-trip would be too late for first paint |
| Sidebar collapse, density | `localStorage` | Device-specific, trivial, non-sensitive | Not worth server storage |
| Recent searches | `localStorage`, capped | Convenience only; device-local | Server persistence implies cross-device meaning it doesn't have |
| Saved filters / views | **Server**, org-scoped | Should follow the user across devices; is real user data (§18.14) | `localStorage` would silently differ per device |
| Default organization | **Server** | Part of the user's profile | — |
| Access token | **Memory only** | Minimizes XSS exfiltration (§6.2) | `localStorage`/`sessionStorage` is script-readable |
| Refresh token | **httpOnly cookie** | Unreadable by script (§6.1) | Any JS-readable storage defeats the purpose |
| Any server entity | **Not persisted** | It would go stale with no invalidation path; and persisting one tenant's data across an org switch is a leak risk | The query cache is the correct, invalidatable home |
| Builder draft recovery | `sessionStorage`, org-tagged, short-lived | Protects unsaved work against a crash or accidental reload (§13.16) | `localStorage` would outlive the session and the tenant context |

The one nuanced case is the builder draft: it is server-derived data being persisted locally, which the
table otherwise forbids. It is permitted because the alternative is silent loss of the user's typing,
it is tagged with the org and paper id and validated on restore, it is cleared on successful save and on
logout, and it lives in `sessionStorage` so it cannot outlive the tab. The exception is bounded and
documented rather than ad hoc.

### 9.10 Teardown on Logout

Logout clears every layer, unconditionally and in a fixed order: cancel in-flight requests → close the
SSE connection → clear the query cache → reset all Zustand stores → clear the builder draft key → drop
the in-memory token → navigate to `/login`. This runs even if the network call to the server fails
(§6.8), because leaving another user's data resident on a shared machine is unacceptable.

### 9.11 Derived State and Selector Discipline

Anything computable from existing state is computed, not stored. Marks totals, distribution
percentages, dirty flags, and permission answers are all derived — storing them creates two sources of
truth that inevitably diverge. Where derivation is expensive (the builder's distribution summaries over
a large paper), it is memoized at a defined boundary (§19.9) rather than cached in a store.

Zustand consumers subscribe with narrow selectors so a component re-renders only when the slice it
actually reads changes. Subscribing to a whole store is the standard way to accidentally re-render half
the application on every keystroke, and it is what makes the builder feel slow if done carelessly
(§13.19).

### 9.12 Cross-Tab Behaviour

Multiple tabs are a real usage pattern (a teacher with the builder open and the question bank in a
second tab). Each tab holds its own in-memory token, query cache, and SSE connection. Coordination is
deliberately minimal:

- **Logout propagates.** A storage-event broadcast tells other tabs to tear down (§9.10), because a
  user who logs out expects to be logged out everywhere on that machine.
- **Notifications de-duplicate** so three tabs do not raise three toasts for one event (§23.11).
- **Everything else stays independent.** No shared-worker leader election in v1 (§11.16) — the added
  complexity is not justified by the cost of N connections at our scale, and independence is more
  robust to a single tab misbehaving.

### 9.13 State Anti-Patterns

| Anti-pattern | Why it is harmful | Do instead |
|--------------|-------------------|------------|
| Copying fetched data into a Zustand store | Two sources of truth; no invalidation path; guaranteed staleness | Read from the query cache via a query hook |
| A single "app store" holding everything | Becomes a junk drawer; every write re-renders broadly | Small stores with explicit exclusion lists (§9.4) |
| Storing derived values | Diverges from its inputs | Derive, memoize if measured to be needed |
| Non-org-scoped query keys | Cross-tenant cache collisions; broken reset | Org-first keys, always (§9.5) |
| Subscribing to an entire store | Re-renders on unrelated changes | Narrow selectors |
| Persisting server data to `localStorage` | Stale forever; tenant-leak risk | Query cache; server-side preferences |
| Leaving optimistic values as final | UI silently disagrees with the server | Always re-settle from the server (§9.8) |
| Patching the cache from a partial event payload | Produces a shape the server never sent | Invalidate unless the payload is patch-grade (§9.6) |
| Form state lifted into global state | Re-renders and lifecycle bugs; loses RHF's performance model | Keep it in React Hook Form (§15) |

### 9.14 State Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Server state owner | TanStack Query, exclusively | Caching, staleness, dedup, invalidation are solved problems; hand-rolling them is where bugs live | A library-specific mental model the team must learn |
| Client state owner | Zustand, several small stores | Minimal boilerplate; narrow selectors; fits our small client footprint (§2.7) | No built-in devtools-grade time travel by default |
| Not Redux | Rejected | Our client-only state is tiny; the reducer/action ceremony would cost more than it returns | Less ecosystem middleware |
| Not React Context for the builder buffer | Rejected | Context re-renders every consumer on every change — unusable for a high-frequency editor | Store adds a dependency and an indirection |
| Query key shape | Org-first, hierarchical, normalized filters | Structural tenant partitioning; predictable invalidation | All contributors must follow the convention (lint/review) |
| Staleness | Per data class, not one global default | Freshness needs differ by two orders of magnitude across classes | A table to maintain as data classes are added |
| SSE → cache | Invalidate by default, patch only for patch-grade payloads | Correctness over round-trip savings | Some extra requests |
| Org switch | Cancel in-flight, full cache reset, SSE reconnect | Eliminates cross-tenant bleed and late-landing responses | Cold cache after a switch |
| Persistence | Device prefs local; user prefs server; never tokens or entities | Matches expectations; minimizes leak surface | Two persistence paths to reason about |
| Cross-tab | Independent, except logout and notification dedup | Simple and robust; avoids leader-election complexity | N connections per user; accepted (§11.16) |

---

## 10. API Communication Architecture

### 10.1 REST as the Contract

The backend exposes REST with a fixed response envelope, so the client speaks REST. Every interaction
is a request against a resource-shaped URL, returning either the success envelope
(`success`, `data`, `message`, `meta.requestId`, `meta.pagination`) or the error envelope
(`success: false`, `error.{code, message, details, ehRef}`, `meta.requestId`).

**Why REST and not GraphQL.** Our read patterns are well-served by purpose-built endpoints with
server-side pagination, filtering, and sorting; the backend already enforces tenant scoping and
authorization per endpoint through the dependency chain, which is a natural fit for REST's one-operation-
per-route model. GraphQL would add a resolver layer, a second authorization surface to audit, and a
query-cost problem, in exchange for flexibility we do not need. The one place GraphQL's benefit would
be real — assembling a dashboard from many sources — we solve with independently-loading widgets
(§12.2) instead.

### 10.2 One Client, Three Layers

**All** network access funnels through a single typed API client in `services/`. Nothing in the
application calls `fetch` directly.

```
┌──────────────────────────────────────────────────────────────────┐
│ Components / Server Components                                    │
│   never fetch directly — they consume hooks or server helpers     │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│ Query hooks  (features/<name>/hooks, TanStack Query)              │
│   owns: query keys, staleness, invalidation, optimistic updates   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│ Feature endpoint wrappers  (features/<name>/api.ts)               │
│   owns: URL shape, request/response types, per-endpoint Zod schema│
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│ apiClient  (services/api-client.ts)                               │
│   owns: base URL, headers, auth, interceptors, retry, timeout,    │
│         cancellation, envelope unwrap, error normalisation        │
└───────────────────────────┬──────────────────────────────────────┘
                            ▼
                    HTTPS → FastAPI API
```

**Why one client.** Cross-cutting behaviour — attaching the token, refreshing it on 401, normalizing
errors, emitting telemetry, enforcing timeouts — must be applied uniformly or it is applied
inconsistently. A single choke point means a change to retry policy or error handling happens once and
is provably complete. It also makes the transport layer testable in isolation and makes "what does the
app talk to?" answerable by reading one directory.

### 10.3 Auth Header Injection Without Coupling to React

The access token lives in the `session` Zustand store (§6.3), but `apiClient` must not import React or
a hook — it runs in event handlers, in retries, and outside the component tree. The client is therefore
given a **token accessor**: a plain function, registered once at bootstrap by the `SessionProvider`,
that returns the current in-memory token and can trigger a refresh.

This keeps the dependency direction clean (`services/` never depends on `providers/` or React) and
means the transport layer works identically in tests, where the accessor is simply stubbed. The
alternative — passing the token into every call site — would leak an auth concern into every feature
and guarantee that someone eventually forgets it.

### 10.4 The Interceptor Chain

```
REQUEST                                                  RESPONSE
────────────────────────────────────────────────────    ───────────────────────────────────────────────
 1. resolve URL (base + path + query serialisation)      1. read status + body
 2. attach headers: Accept, Content-Type,                2. validate envelope shape (Zod)
    Accept-Language                                      3. success? → unwrap `data`,
 3. attach Authorization: Bearer <in-memory token>          surface `meta.pagination`,
 4. attach X-Request-Id (client-generated correlation)      record `meta.requestId`
 5. attach Idempotency-Key (mutations that support it)   4. error? → normalise to ApiError
 6. serialise body                                       5.   401 → single-flight refresh, replay (§10.9)
 7. bind AbortSignal (unmount, supersede, timeout)       6.   retryable? → backoff + jitter, re-enter
 8. start timing mark                                    7. emit telemetry (timing, status, route)
                                                         8. return typed data | throw ApiError
```

The ordering matters in two places. Auth is attached **after** the request is otherwise complete, so a
replayed request after a refresh picks up the new token rather than the stale one it was built with.
And telemetry is emitted **after** normalization, so every recorded error has a stable `code` and
`ehRef` to group by (§26.6).

### 10.5 Request/Response Lifecycle

```
Component        Query hook       api.ts        apiClient        API (dependency chain)
    │                │               │              │                    │
    │ render         │               │              │                    │
    ├───────────────▶│ useQuery(key) │              │                    │
    │                ├──cache hit?──▶│  (fresh → return cached, no I/O)  │
    │                │               │              │                    │
    │                ├──────────────▶│ typed call   │                    │
    │                │               ├─────────────▶│ build + intercept  │
    │                │               │              ├───────────────────▶│ get_current_user
    │                │               │              │                    │ get_org_context
    │                │               │              │                    │ require_roles
    │                │               │              │                    │ Scope Filter
    │                │               │              │◀───────────────────┤ 200 + envelope
    │                │               │◀─ unwrap ────┤ validate, unwrap   │
    │                │◀─ typed data ─┤              │                    │
    │◀── re-render ──┤ cache write   │              │                    │
```

The diagram makes one point explicit: **the dependency chain runs on every single request**, including
requests the client believed were permitted (§7.1). The client's projection never substitutes for it.

### 10.6 Envelope Handling and Boundary Validation

The client validates the envelope with Zod **at the boundary**, before the payload reaches any feature
code. This is deliberate and worth defending, because it costs CPU on every response:

- TypeScript types are erased at runtime. Without a parse step, a backend change (a renamed field, a
  now-nullable value) produces `undefined` deep inside a component and a crash far from the cause.
- Parsing at the boundary turns that into a single, loud, attributable failure with the `requestId`
  attached — reported to monitoring (§26.6) rather than discovered by a user.
- It is also a modest security control: it constrains what shapes can flow into rendering logic.

Three things are extracted from every response: `data` (unwrapped and returned), `meta.pagination`
(handed to the query layer for cursor/page continuation), and `meta.requestId` (attached to telemetry
and to any error surfaced to the user, so a support ticket can be joined to a backend log).

Validation is **tolerant on unknown fields** (§10.15) — additive backend changes must not break an
older client.

### 10.7 Error Normalisation

Every failure — HTTP error, network error, timeout, abort, envelope-parse failure — becomes one
client-side error type:

```
ApiError {
  status      // HTTP status, or 0 for network/abort
  code        // error.code from the envelope, or a synthetic client code
  message     // human-readable, already mapped for display
  details     // field-level details (drives 422 → form field mapping, §15.7)
  ehRef       // EH-n reference from the backend taxonomy
  requestId   // correlation id for support and log joining
  retryable   // computed: may this be retried automatically?
}
```

Feature code therefore never inspects raw responses or status codes; it branches on `code` and
`retryable`. This is what lets §25 define one consistent presentation policy for the whole
application rather than per-endpoint ad-hockery.

| Status | Meaning here | Client behaviour | User-facing treatment |
|--------|-------------|------------------|----------------------|
| 400 | Business rule violated (e.g. blueprint infeasible, quota rule) | No retry; surface `error.message` | Inline alert on the surface that triggered it, with the recovery action |
| 401 | Access token missing/expired/invalid | Single-flight refresh, replay once; on second 401 → hard logout | Invisible when refresh succeeds; otherwise redirect to `/login?next=…` |
| 403 | `require_roles` or the scope filter denied | No retry; invalidate the related query so the UI re-syncs | Toast for an action; `<Forbidden/>` page for a route (§7.6) |
| 404 | Resource absent, or scoped away by tenant isolation | No retry | Segment `not-found.tsx`; never "you lack access", since 404 may be deliberate scoping |
| 409 | Optimistic-lock conflict or duplicate | No blind retry; hand to the conflict flow | Conflict dialog in the builder (§13.16); "already exists" on the field for duplicates |
| 422 | Validation failed | No retry; map `error.details` onto form fields | Field-level errors + form summary (§15.7) |
| 429 | Rate limited | Respect `Retry-After`; throttle the surface; retry once for idempotent reads | "Too many requests — retrying in Ns", with the control disabled during cooldown |
| 500 | Server fault | Retry idempotent reads with backoff; never mutations | Section-level error state with Retry and the support reference |
| 502 | LLM/upstream failure | Retry only if the endpoint is a safe read; generation failures arrive as job events, not here | "The AI service is temporarily unavailable" + retry the job (§13.18) |
| Network failure | DNS/offline/connection reset | Retry idempotent with backoff; enter offline mode if persistent | Offline banner (§25.7); queued rather than failed where safe |
| Timeout | Exceeded the endpoint-class budget | Same as network failure, but never for a mutation already sent | "This is taking longer than expected" + Retry |

The 404 row deserves emphasis: because `Scope Filter` returns 404 rather than 403 for out-of-tenant
resources, the client must not translate 404 into an access message. Doing so would leak the existence
of another tenant's records — the exact thing the backend's choice of 404 is designed to prevent.

### 10.8 Retry Policy

| Class | Retried? | Attempts | Backoff | Reason |
|-------|---------|----------|---------|--------|
| `GET`/`HEAD` on network failure, timeout, 500, 502, 429 | Yes | 3 | Exponential (≈250 ms base) + full jitter | Idempotent by definition; transient faults are common and cheap to re-attempt |
| `POST`/`PATCH`/`DELETE` — no idempotency key | **No** | 1 | — | The server may have applied the change before the response was lost; a retry could duplicate a paper, an invite, or a charge |
| `POST` with `Idempotency-Key` | Yes | 2 | Exponential + jitter | The key makes the operation safe to repeat; the server de-duplicates |
| 4xx other than 429 | No | 1 | — | Deterministic; retrying produces the same failure and wastes the user's time |
| Aborted (unmount/supersede) | No | — | — | Not a failure at all (§25.8) |

Jitter is full, not fixed: without it, a transient backend blip causes every open tab to retry in the
same instant, converting a small incident into a stampede. Retries also share a **budget** — if more
than a small fraction of recent requests are retries, the client stops retrying and degrades to
explicit user-initiated retry, so a struggling backend is not pushed further by its own clients.

### 10.9 Single-Flight Refresh and Replay

```
req A ─401──┐
req B ─401──┤──▶ refresh in flight?  no ──▶ POST /auth/refresh ──┐
req C ─401──┘                        yes ──▶ await the same promise
                                                                 │
                            ┌────────────────────────────────────┘
                            ▼
                   success → replay A, B, C once with the new token
                   failure → reject A, B, C; hard logout (§6.7)
```

A burst of concurrent 401s is the normal case, not the exception — a dashboard fires several queries at
once and they expire together. Without single-flight, each would trigger its own refresh; because the
refresh token is **single-use with rotation**, the second and third calls would present an
already-rotated token and be treated as **reuse**, revoking the session family and logging the user
out. Single-flight is therefore not an optimization — it is required for correctness against the
backend's reuse detection.

Each request is replayed at most once. A 401 on the replay means the problem is not token freshness,
and the client stops rather than looping.

### 10.10 Timeouts

| Endpoint class | Budget | Reason |
|----------------|--------|--------|
| Interactive read (lists, detail, facets) | 10 s | Beyond this the user has already given up; better to fail and offer Retry |
| Mutation (save, update, invite) | 20 s | Writes may involve more server work; a premature timeout risks ambiguity about whether it applied |
| Job submission (generate, export, ingest) | 15 s | Only the *acceptance* is awaited; the work itself is asynchronous |
| Upload handshake (initialise/complete) | 15 s | Small control-plane calls; the bytes go elsewhere (§17) |
| Direct object-storage PUT | No client timeout; stall detection instead | Large files legitimately take minutes; a fixed timeout would fail valid uploads (§17.7) |
| Analytics / report queries | 30 s | Heavier aggregate reads over rollups |

**Why AI work is never a long-held request.** A generation can take minutes. Holding an HTTP request
open for it would consume a server connection, break through proxy idle timeouts, give the client no
progress information, and lose everything on a transient network blip. Instead the API returns
`202 Accepted` with a job handle and progress flows over SSE (§11). The request budget then covers only
"was the job accepted?", which is fast and deterministic.

### 10.11 Cancellation and Deduplication

Every request carries an `AbortSignal`. Three sources cancel:

| Trigger | Example | Why it matters |
|---------|---------|----------------|
| Component unmount / route change | Navigating away from a list mid-fetch | Frees the connection; prevents a state update against a dead surface |
| Superseded request | Each keystroke in the question-bank search | Prevents the stale-response race where an older, slower response overwrites a newer one (§18.7) |
| Timeout | Budget exceeded | Uniform mechanism rather than a second timing path |
| Org switch | Any in-flight request at switch time | Prevents a response scoped to the previous tenant landing in the new tenant's cache (§9.7) |

The query layer additionally **coalesces** identical in-flight requests: three components mounting with
the same query key produce one network call. This is why the org-first, normalized key convention
(§9.5) has a performance payoff as well as a correctness one — keys that differ only in property order
would defeat coalescing entirely.

Aborted requests are **not errors** and never surface as failures (§25.8).

### 10.12 Rate Limiting and Conflicts

**429.** The client honours `Retry-After`, disables the triggering control for the indicated window,
and shows the remaining time rather than a bare failure. Chatty surfaces are additionally throttled at
source — search debouncing (§18.6), coalesced progress writes (§11.13), and a minimum interval between
feasibility checks (§13.4) — because the correct response to a rate limit is to stop generating the
traffic, not to retry harder.

**409.** Two distinct meanings share this status, and the client distinguishes them by `error.code`:
an **optimistic-lock conflict** (the resource changed since the version the client held) routes into
the conflict-resolution flow (§13.16); a **duplicate** (a template name already used in the org) maps
onto the offending form field like a validation error. Neither is retried blindly — retrying a lock
conflict would simply overwrite someone's work.

### 10.13 Caching Responsibilities

| Layer | Caches | Typical lifetime | Owns |
|-------|--------|-----------------|------|
| CDN / edge | Static assets, immutable build output | Long, content-hashed | Deployment (§09) |
| Server Components / server-side fetch cache | Server-rendered read data, reference data | Short, revalidated | Next.js runtime |
| HTTP cache headers | Signed download URLs, public assets | Per resource | Backend + object storage |
| TanStack Query cache | All client-side server state | Per data class (§9.5) | Frontend |
| Browser storage | Device preferences only | Until cleared | Frontend (§9.9) |

The rule that keeps these from colliding: **no API response that is org-scoped is ever cached by a
shared layer**. CDN and public HTTP caches see only static assets and short-lived signed URLs; anything
tenant-specific is cached exclusively in the per-user query cache, keyed org-first.

### 10.14 Pagination, Filtering, Sorting

The client emits the backend's conventions verbatim:

| Concern | Parameter | Notes |
|---------|----------|-------|
| Page | `page`, `pageSize` | `pageSize` defaults to 20, capped at 100; the client never requests more |
| Cursor | `cursor` | Used for large or frequently-changing reads (§10.14 below) |
| Filter | `filter[key]=value` | Repeated per key; empty values omitted entirely rather than sent blank |
| Free text | `q=` | Debounced, minimum length enforced client-side (§18.6) |
| Sort | `sort=-createdAt,title` | Leading minus is descending; multiple keys comma-separated |

**Offset vs cursor.** Offset pagination is used where the user needs orientation — numbered pages, "of
N results", jumping to a page (admin grids, approvals queue). Cursor pagination is used for infinite
lists over large, mutating collections (question bank browse, audit log, notification history), because
offsets drift when rows are inserted or removed between requests, producing duplicated or skipped
items. The choice is a per-surface decision recorded in §18.9, not a global one.

Pagination metadata always arrives in `meta.pagination`, so the query layer never has to parse links or
guess whether more data exists.

### 10.15 Versioning and Forward Compatibility

The client is a **tolerant reader**: Zod schemas at the boundary allow unknown fields to pass through
without failing, so an additive backend change deploys safely ahead of a client release. The client
never sniffs a version to decide behaviour; where a capability may or may not exist, it **detects the
feature** — checking whether the capability mirror (§7.2) or a response field advertises it — rather
than inferring from a version number. Feature detection degrades gracefully when the two sides deploy
out of order, which they routinely will.

Breaking changes are handled by the backend's URL versioning; the client pins the version it was built
against and is updated deliberately, with the contract change tracked in §30.

### 10.16 Observability Hooks

Every request emits a timing sample (endpoint class, status, duration, retry count) and every
normalized error emits a structured event carrying `code`, `ehRef`, `requestId`, route, and role.
Measuring from the client is not redundant with backend metrics: it captures the network, the device,
and the user's real conditions, which is where a "the app is slow" report usually originates (§26.4).

### 10.17 Security Notes

The client holds no secrets — anything in the bundle is public (§24.13). It talks to the API origin
directly with a bearer token; the only same-origin BFF route handlers are the ones that must set or
read the httpOnly refresh cookie (login, refresh, logout) and the SSE proxy where used (§11.8). The
client never asserts an `org_id` in a request body: tenant context comes from the `activeOrgId` claim
in the signed token, so a tampered client cannot address another tenant (§7.7, §24.7).

### 10.18 API Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Transport | REST via one typed client | Matches the backend; single choke point for cross-cutting behaviour | Multiple round-trips for composite screens; solved by widget composition |
| Not GraphQL | Rejected | No resolver layer or second authorization surface to audit; REST fits per-endpoint guards | Less client-side query flexibility |
| Boundary validation | Zod parse on every response | Turns silent shape drift into one loud, attributable failure | CPU per response; schema maintenance |
| Error model | One normalized `ApiError` | Uniform handling and presentation across the app (§25) | Must map every backend code |
| Long work | `202 Accepted` + SSE | No held connections, real progress, survives blips | Two mechanisms to reason about |
| Mutation retries | Never blind-retry; idempotency key otherwise | Prevents duplicate papers, invites, charges | Some transient failures surface to the user |
| Refresh | Single-flight with replay | Required by single-use refresh rotation, not just efficiency | Extra coordination in the interceptor |
| 404 for out-of-tenant | Presented as not-found, never as access-denied | Avoids leaking existence across tenants | Slightly less precise messaging |
| Pagination | Offset where orientation matters, cursor for large mutating lists | Correctness over uniformity | Two pagination paths in the query layer |
| Versioning | Tolerant reading + feature detection | Safe out-of-order deploys | Cannot rely on strict response shapes |

---

## 11. Real-Time Communication

### 11.1 Why SSE

Three of the platform's core operations are long-running background jobs: textbook OCR and ingestion,
paper generation, and export rendering. The client needs to know how they are progressing. The
requirement is strictly **server → client**; the browser has nothing to say mid-job.

| | SSE | WebSocket | Long polling | Short polling |
|---|-----|-----------|--------------|---------------|
| Directionality | Server → client | Bidirectional | Server → client | Server → client |
| Fits our need | Exactly | Over-provisioned | Yes, awkwardly | Yes, wastefully |
| Transport | Plain HTTP | Upgrade handshake | Plain HTTP | Plain HTTP |
| Proxy/CDN friendliness | Good (needs buffering disabled) | Sometimes blocked by corporate proxies | Good | Good |
| Auth | Same as any HTTP request | Separate handshake auth path | Same as HTTP | Same as HTTP |
| Reconnect | Built in, with `Last-Event-ID` | Hand-rolled | Hand-rolled | N/A |
| Server cost | One held connection per tab | One held connection + framing | Held connection per poll cycle | Many short requests |
| Latency | Low | Low | Moderate | Poor (interval-bound) |
| Browser limits | 6 per origin on HTTP/1.1; unlimited under HTTP/2 | Similar | Similar | N/A |

SSE wins because it is the smallest thing that solves the problem. WebSockets would give us a
bidirectional channel we do not use, a separate authentication path, and reconnection logic we would
have to write ourselves. Polling would either waste requests or add latency proportional to the
interval — and progress updates are exactly where latency is visible. Schools frequently sit behind
restrictive corporate or institutional proxies, where plain HTTP streaming survives more reliably than
a protocol upgrade.

The one place this decision would be revisited is collaborative editing (§28.4), which genuinely needs
a bidirectional channel. That is a feature-scoped change, not a reason to over-build now.

### 11.2 End-to-End Path

```
┌──────────┐  publish   ┌───────────┐  subscribe  ┌─────────────┐   SSE    ┌──────────────────┐
│ Worker   │──────────▶ │  Redis    │ ──────────▶ │  API        │ ───────▶ │ Browser          │
│ OCR/gen/ │  channel:  │  pub/sub  │             │  SSE        │  text/   │  ┌────────────┐  │
│ export   │  org:{id}  │           │             │  endpoint   │  event-  │  │  SSE hub   │  │
└──────────┘            └───────────┘             │ (guard      │  stream  │  └─────┬──────┘  │
                                                  │  chain)     │          │        │         │
┌──────────┐  publish                             └─────────────┘          │  ┌─────▼──────┐  │
│ API      │───────────────────▲                                           │  │ query cache│  │
│ services │                                                               │  │  + UI      │  │
└──────────┘                                                               │  └────────────┘  │
                                                                           └──────────────────┘
```

The API's SSE endpoint is subject to the same dependency chain as any other request
(`get_current_user → get_org_context → require_roles → scope`), so a stream only ever carries events for the
authenticated user's active organization. Tenant isolation on the event path is a server property, not
something the client filters after the fact.

### 11.3 One Multiplexed Connection Per Tab

The client opens **exactly one** SSE connection per tab, carrying every channel the user is entitled to
in their active org, rather than one connection per job or per feature.

**Why.** Under HTTP/1.1 browsers cap concurrent connections at roughly six per origin; a per-job
connection would exhaust that quickly during a bulk ingestion and then silently starve ordinary API
requests — a particularly nasty failure because it looks like the whole app has hung. HTTP/2
multiplexing removes the hard cap, but per-connection server cost does not vanish, and we cannot assume
HTTP/2 on every network path. One connection also gives one lifecycle to manage, one reconnect policy,
one place to attach auth, and one point at which to reconcile after a gap.

The cost is a fan-out concern: the hub must route each event to the right subscribers, and an
unsubscribed event type still crosses the wire. Both are cheap relative to the alternative.

### 11.4 The SSE Hub

The hub is an application-level singleton, mounted in the `(auth)` layout (§8.2), that owns the
connection and exposes a subscription API to the rest of the app:

| Responsibility | Detail |
|----------------|--------|
| Connection ownership | Opens on session establishment, closes on logout, reconnects on org switch (§11.7) |
| Lifecycle management | Backoff, resume, heartbeat monitoring, fallback (§11.9–§11.14) |
| Routing | Dispatches each event to subscribers registered by event type and optional resource id |
| Cache integration | Applies the patch-or-invalidate rule (§11.6) against the query cache |
| Coalescing | Batches high-frequency progress events before they reach React (§11.13) |
| Deduplication | Drops events already seen by `eventId` within a bounded window |
| Telemetry | Emits connection health metrics (§26.5) |

Components never touch the connection. They subscribe declaratively — "tell me about generation
progress for paper X" — and unsubscribe on unmount. This keeps the connection's lifetime decoupled from
any component's lifetime, which is what allows a job to keep updating the cache while the user browses
elsewhere (§23.6).

### 11.5 Event Envelope and Catalogue

Every event carries enough identity to be routed and applied without an additional fetch:

```
{ eventId, type, orgId, resourceType, resourceId, sequence, occurredAt, payload }
```

`sequence` is monotonic per resource, which is what makes out-of-order and duplicate delivery safe to
handle (§11.12). `orgId` is present so the client can assert the event matches its active org and drop
anything that does not — a defence-in-depth check against a stale connection after an org switch, not a
substitute for server-side scoping.

| Event | Producer | Payload outline | Patch-grade? | Consumers | UI effect |
|-------|----------|-----------------|--------------|-----------|-----------|
| `textbook.progress` | OCR worker | stage, percent, pagesDone/total | Yes (progress only) | Textbooks module | Progress bar and stage label on the textbook card (§12.4) |
| `textbook.parsed` | OCR worker | textbookId, chapterCount, questionCandidates, warnings | No | Textbooks, question bank | Invalidate textbook detail and question lists; success/partial toast |
| `textbook.failed` | OCR worker | textbookId, reason, ehRef | No | Textbooks | Failure state on the card with the recovery action (§25.6) |
| `paper.generation.progress` | Generation worker | jobId, paperId, stage (retrieval/gap-fill/assembly), percent, filledSlots | Yes | Paper Builder | Per-slot and overall progress in the builder (§13.8) |
| `paper.question.ready` | Generation worker | paperId, sectionId, slotId, question object | Yes | Paper Builder | Question materialises into its card as it arrives |
| `paper.generated` | Generation worker | paperId, version, counts, warnings | No | Paper Builder, papers list | Invalidate paper detail and list; completion toast |
| `paper.generation.failed` | Generation worker | jobId, paperId, reason, ehRef, partial | No | Paper Builder | Failure state with retry; partial results preserved (§13.18) |
| `paper.approval.changed` | API service | paperId, fromState, toState, actorId | No | Approval, papers, dashboard | Invalidate paper + approvals queue; notify author or approvers (§12.7) |
| `export.progress` | Export worker | exportId, percent | Yes | Export flow | Progress in the background-jobs indicator |
| `export.ready` | Export worker | exportId, paperId, expiresAt | No | Export flow, notifications | Completion toast with a download action (§29.6) |
| `export.failed` | Export worker | exportId, reason, ehRef | No | Export flow | Failure notification with retry |
| `notification.created` | API service | notificationId, ntType, title, body, link | Yes | Notification hub | Badge increment, toast where warranted, centre entry (§23) |
| `quota.warning` | Metering service | metric, used, limit, threshold | No | Billing, builder | Escalating banner; blocks expensive actions at the hard limit (§12.9) |
| `heartbeat` | API | serverTime | — | Hub only | Liveness; no UI effect (§11.11) |

The **patch-grade** column is the operational form of the rule in §9.6. It is a property of the event's
contract, decided once with the backend, not a judgement made at each call site.

### 11.6 Patch or Invalidate

- **Patch** when the payload is authoritative and complete for what it touches: a progress percentage,
  a fully-formed question object, a notification record. Patching updates the UI in the same frame the
  event arrives, with no round-trip.
- **Invalidate** when the event is a *signal*: "this paper was approved", "ingestion produced N
  questions", "quota was recalculated". These imply changes across records, derived fields, and
  permissions the event does not carry.

The bias is toward invalidation, because a wrong patch produces a UI that quietly disagrees with the
server and is very hard to notice. The extra request buys certainty.

### 11.7 Connection Lifecycle

```
        ┌────────┐  session established
        │  idle  │──────────────────────┐
        └────────┘                      ▼
             ▲                    ┌────────────┐  stream open   ┌────────┐
             │                    │ connecting │───────────────▶│  open  │
             │                    └────────────┘                └───┬────┘
             │                          ▲                           │
             │                          │ backoff elapsed           │ no heartbeat
             │                          │                           │ within window
             │                    ┌─────┴──────┐   error / EOF  ┌───▼──────┐
             │                    │reconnecting│◀───────────────│ degraded │
             │                    └─────┬──────┘                └───┬──────┘
             │                          │ max attempts reached      │ heartbeat resumes
             │                          ▼                           └──────────▶ open
             │                    ┌──────────┐
             │                    │ polling  │  (fallback, §11.14)
             │                    │ fallback │
             │                    └──────────┘
        ┌────┴─────┐  logout / org switch / tab close
        │  closed  │◀──────────────────────────────────
        └──────────┘
```

| Transition | Trigger |
|-----------|---------|
| idle → connecting | Session established in the `(auth)` layout |
| connecting → open | First byte / first event received |
| open → degraded | No heartbeat within the expected window (§11.11) |
| degraded → open | Heartbeat or any event resumes |
| open/degraded → reconnecting | Stream error, EOF, or server restart |
| reconnecting → connecting | Backoff interval elapsed |
| reconnecting → polling fallback | Repeated failures beyond the attempt ceiling |
| any → closed | Logout, org switch, or tab close |
| closed → connecting | Org switch completes with a new token (§9.7) |

### 11.8 Authenticating the Stream

The native `EventSource` API cannot set request headers, which rules out attaching
`Authorization: Bearer` the way every other request does. Three options, one acceptable:

| Option | Verdict | Reasoning |
|--------|---------|-----------|
| Token in the query string | **Rejected** | Tokens land in access logs, proxy logs, browser history, and `Referer` headers. A short-lived token does not make that acceptable (§24.5) |
| Cookie-authenticated stream | Rejected | Would require the API to accept cookie auth, adding a CSRF-exposed authentication path purely for one endpoint |
| **Fetch-based streaming client** (or a same-origin route handler proxy that attaches the header server-side) | **Chosen** | The `Authorization` header is sent exactly as on every other request; the dependency chain applies unchanged |

The fetch-based reader costs us `EventSource`'s built-in reconnection, so the hub implements reconnect
and `Last-Event-ID` resume itself (§11.9). That is a fair trade: we needed custom backoff,
reconciliation, and fallback behaviour anyway.

**Mid-stream token expiry.** The access token expires roughly every 15 minutes, while a stream may live
for hours. The hub subscribes to the session's refresh cycle and performs a **proactive reconnect**
shortly after each refresh, carrying `Last-Event-ID` so nothing is missed. Reconnecting on a schedule we
control is far better than being disconnected by the server at an arbitrary moment.

### 11.9 Reconnection and Resume

Backoff is exponential with full jitter (roughly 1 s → 2 s → 4 s → 8 s …, capped at 30 s). The cap
matters: an unbounded backoff leaves a user staring at a stale screen long after the network recovered.
The jitter matters more — after a server restart, every connected client would otherwise reconnect in
the same instant and knock the API over again.

On reconnect the client sends `Last-Event-ID`. The server replays events after that id from a **bounded
buffer**; if the gap exceeds the buffer, the server signals that resume was incomplete.

### 11.10 Missed-Event Recovery

Resume alone is never trusted. On **every** successful reconnect the hub triggers a **reconciliation
refetch**: invalidate the queries backing whatever the user is currently looking at, plus any jobs known
to be in flight. Cost is a handful of requests at reconnect time; benefit is that the UI converges on
server truth regardless of what the replay buffer did or did not contain. Given that a missed terminal
event ("your paper is ready") would otherwise leave the user waiting forever, the trade is obvious.

### 11.11 Heartbeat and Idle Detection

The server emits a periodic `heartbeat`. Its purpose is not liveness reporting to the user but
detecting the failure mode SSE is most prone to: a connection that appears open at the socket level
while an intermediary has silently stopped forwarding data. If no event or heartbeat arrives within the
expected window, the hub moves to `degraded` and then reconnects. Heartbeats also keep proxies from
closing an idle connection.

### 11.12 Ordering and Idempotency

Events may arrive out of order or more than once, especially around a reconnect with replay. Every
consumer must therefore be **idempotent**:

| Event kind | Rule |
|-----------|------|
| Monotonic progress | Apply only if `sequence` (or percent) exceeds what is already applied — never let a replayed 40% overwrite a live 80% |
| Terminal state | Idempotent by construction; applying "completed" twice is harmless |
| Record creation (`paper.question.ready`, `notification.created`) | Keyed by resource id; a duplicate replaces rather than appends |
| Invalidation signals | Naturally idempotent; the second invalidation is a no-op if data is already fresh |

Duplicate suppression by `eventId` within a bounded window catches the common case cheaply; the
per-consumer rules above are the correctness guarantee.

### 11.13 Backpressure and Coalescing

A generation job can emit progress many times per second. Writing each event straight into the query
cache would trigger a React render per event and make the builder stutter precisely when the user is
watching it most closely. The hub therefore **coalesces**: progress events for the same resource are
collapsed to the latest value and flushed on an animation frame, so render work is bounded by frame
rate rather than event rate. Non-progress events (a question arriving, a job completing) are never
coalesced — they carry distinct information and must all be applied.

If the event rate exceeds a sane ceiling, the hub logs it as an anomaly (§26.5) rather than trying to
absorb an unbounded stream.

### 11.14 Polling Fallback

If the stream cannot be established after the attempt ceiling — a proxy that buffers `text/event-stream`
into uselessness, a network that blocks streaming, a persistent server fault — the hub degrades to
polling the job-status and notification endpoints on a modest interval (roughly 10 s while a job is
active, 60 s otherwise), with the interval backing off while the tab is hidden.

The user sees a subtle "live updates unavailable — refreshing periodically" indicator. Honesty here is
deliberate (§2.10): the product keeps working, but the user knows updates are slower, so they do not
misread a delay as a stall. The hub retries the stream periodically and returns to it silently on
success.

### 11.15 Tab Visibility and Sleep

While the tab is hidden the connection is kept open — a completing job should still update the cache so
the surface is correct when the user returns — but UI-side work is reduced: coalescing windows lengthen
and toasts for background jobs queue rather than fire (§23.11). After a device sleep the connection is
usually dead without an error event; the heartbeat check catches it on wake and triggers reconnect plus
reconciliation.

### 11.16 Multi-Tab Behaviour

Each tab holds its own connection. A shared worker with leader election would reduce connections to one
per browser, and was considered and rejected for v1: it adds a coordination protocol, a failover path
when the leader tab closes, and a debugging surface that is disproportionate to the cost of N
connections at our expected concurrency. Independence is also more robust — one wedged tab cannot
starve the others. The two genuinely user-visible multi-tab problems, duplicate toasts and badge drift,
are solved directly (§23.11, §9.12) rather than by centralizing the transport.

### 11.17 Failure Handling

| Failure | Detection | Client behaviour | User-visible |
|---------|----------|------------------|--------------|
| Proxy buffers the stream | No events despite an open connection; heartbeat window elapses | Degrade → reconnect → polling fallback | "Live updates unavailable" indicator |
| HTTP/1.1 connection cap reached | Stream fails to open while API calls also stall | Single-connection design prevents it; hub refuses to open a second | None |
| Server restart / deploy | Stream EOF | Backoff + jitter reconnect, resume, reconcile | Brief indicator; no data loss |
| Network loss | Stream error + failing requests | Offline mode (§25.7); reconnect on `online` | Offline banner; work preserved |
| Token expired mid-stream | Server closes with an auth error | Refresh, then reconnect with `Last-Event-ID` | Invisible |
| Replay window exceeded | Server signals incomplete resume | Full reconciliation refetch of active surfaces | Brief loading state |
| Event storm | Rate over ceiling | Coalesce; log anomaly | Smooth progress; no stutter |
| Malformed event | Envelope parse fails | Drop the event, report to monitoring, keep the stream | None |
| Event for another org | `orgId` mismatch | Drop and force reconnect | None |

### 11.18 Generation With a Mid-Stream Reconnect

```
 Browser (hub)          API (SSE)         Redis pub/sub        Generation Worker
     │                      │                   │                      │
     │ POST /papers/{id}/generate               │                      │
     ├─────────────────────▶│ validate, enqueue │                      │
     │◀─ 202 { jobId } ─────┤                   ├─────────────────────▶│ start
     │                      │                   │                      │
     │  (subscribed to paper.* for {paperId})   │◀── progress 10% ─────┤ retrieval
     │◀── paper.generation.progress seq=1 ──────┤                      │
     │◀── paper.question.ready  seq=2 ──────────┤◀── slot filled ──────┤ AI gap-fill
     │      (patch: question materialises)      │                      │
     │                      │                   │                      │
     │  ✗ connection drops (proxy timeout)      │◀── progress 60% ─────┤   (seq=3 missed)
     │                      │                   │                      │
     │  backoff 1s + jitter │                   │                      │
     ├── reconnect, Last-Event-ID: seq=2 ──────▶│                      │
     │◀── replay seq=3 (progress 60%) ──────────┤                      │
     │◀── seq=4 paper.question.ready ───────────┤◀── slot filled ──────┤
     │  + reconciliation refetch of paper detail│                      │
     │                      │                   │◀── done ─────────────┤ assembly
     │◀── paper.generated seq=5 ────────────────┤                      │
     │   invalidate paper detail + list; toast  │                      │
```

Note the two safety nets working together: `Last-Event-ID` recovers the buffered gap, and the
reconciliation refetch covers anything the buffer could not. Neither is trusted alone.

### 11.19 Real-Time Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Transport | SSE | Our need is server→client only; plain HTTP, proxy-friendly, resumable | One-way; would be revisited for collaborative editing (§28.4) |
| Connection count | One multiplexed per tab | Avoids HTTP/1.1 connection starvation; one lifecycle to manage | Hub must fan out; unsubscribed events still cross the wire |
| Ownership | Singleton hub in the `(auth)` layout | Connection outlives components, so jobs update the cache while the user navigates | Hub is a critical singleton to test carefully |
| Stream auth | Fetch-based streaming with the `Authorization` header | No token in URLs; guard chain applies unchanged | Must implement reconnect/resume ourselves |
| Mid-stream expiry | Proactive reconnect after refresh | Controlled reconnect beats an arbitrary server-side disconnect | Periodic brief reconnects |
| Recovery | `Last-Event-ID` **and** reconciliation refetch | Replay buffers are bounded; a missed terminal event would strand the user | Extra requests at reconnect |
| Cache updates | Patch only patch-grade payloads, else invalidate | A wrong patch is silent and hard to detect | Some extra round-trips |
| High-frequency events | Coalesce and flush per frame | Bounds render work by frame rate, not event rate | Sub-frame progress detail is discarded (worthless anyway) |
| Degradation | Polling fallback with a visible indicator | Product keeps working; user is not misled | A second update path to maintain |
| Multi-tab | Independent connections, no leader election | Simpler and more robust; N connections are affordable | More server connections per user |

---

## 12. Feature Module Architecture

### 12.0 Module Map

Twelve feature modules cover the entire product surface. Each follows the uniform anatomy defined in
§4.4 (`components/`, `hooks/`, `api.ts`, `schema.ts`, `types.ts`, optional `store.ts`, and an
`index.ts` that is the module's **only** public surface).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ SHELL-RESIDENT (loaded with the app shell, always present)                   │
│   auth  ·  notifications  ·  profile(menu)                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ TEACHER SURFACES (lazy per route)                                            │
│   dashboard · question-bank · textbooks · paper-builder · templates(read)     │
├─────────────────────────────────────────────────────────────────────────────┤
│ ADMIN SURFACES (lazy per route)                                              │
│   approval · templates(manage) · analytics · billing · settings              │
├─────────────────────────────────────────────────────────────────────────────┤
│ PLATFORM SURFACES (lazy per route, super_admin only)                         │
│   platform analytics · orgs · prompt-versions · audit                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

| # | Module | Routes owned | teacher | coaching_admin | super_admin |
|---|--------|--------------|:-------:|:--------------:|:-----------:|
| 12.1 | Authentication | `/login`, `/forgot-password`, `/accept-invite/[token]`, `/select-org` | ✓ | ✓ | ✓ |
| 12.2 | Dashboard | `/dashboard` | ✓ | ✓ | ✓ |
| 12.3 | Question Bank | `/question-bank`, `/question-bank/[questionId]` | ✓ | ✓ | ✓ |
| 12.4 | Textbook Upload | `/textbooks`, `/textbooks/[textbookId]` | ✓ | ✓ | ✓ |
| 12.5 | Paper Builder | `/papers`, `/papers/[paperId]`, `/papers/[paperId]/build` | ✓ | ✓ | ✓ |
| 12.6 | Templates | `/admin/templates` (manage); template picker embedded in the builder | read | ✓ | ✓ |
| 12.7 | Approval | `/admin/approvals` | – | ✓ | ✓ |
| 12.8 | Analytics | `/admin/analytics`, `/platform/analytics` | – | org | org + platform |
| 12.9 | Billing | `/admin/billing`; quota surfaces app-wide | read-only meter | ✓ | ✓ |
| 12.10 | Settings | `/settings`, `/admin/programs`, `/admin/users`, `/platform/orgs`, `/platform/prompt-versions`, `/platform/audit` | personal only | org scope | platform scope |
| 12.11 | Notifications | `/notifications`; bell and toasts app-wide | ✓ | ✓ | ✓ |
| 12.12 | Profile | `/profile` | ✓ | ✓ | ✓ |

**The boundary rule.** Modules communicate through exactly four channels: another module's `index.ts`
exports, shared components in `components/shared`, shared types in `types/`, and — most importantly —
the **TanStack Query cache**. A module that needs a paper does not ask the paper-builder module for it;
it reads `[orgId, 'papers', paperId]` from the cache through a shared query hook. This is what keeps
twelve modules from becoming a dependency graph nobody can reason about, and it is why the invalidation
matrix in §9.6 is the real coupling surface rather than the import graph.

### 12.1 Authentication

Owns everything between "a browser arrives" and "a session exists in an organization." The mechanics —
token model, refresh, org switching, failure paths — are specified in §6; this module owns the
*screens* and the *bootstrap sequence*.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Login, forgot/reset password, invite acceptance, organization selection, session bootstrap, silent refresh scheduling, logout and teardown |
| Screens / routes | `/login`, `/forgot-password`, `/reset-password/[token]`, `/accept-invite/[token]`, `/select-org` |
| Roles | All, pre-session |
| Backend endpoints | `POST` login · `POST` refresh · `POST` logout · `POST` switch-org · `POST` password reset request/confirm · `GET`/`POST` invite · `GET` me + capabilities |
| Server state | `['me']`, `['me','capabilities']` — session-length staleness, org-independent by design |
| Client state | `session` store (§6.3) — the only module that writes it |
| Real-time | None; the SSE hub is *started* by this module's bootstrap but owned by §11.4 |
| Key validations | Email format, password policy on reset, invite token presence; credential correctness is server-only |

**Bootstrap sequence.** On first load the client has no access token (memory is empty by design) but may
hold a refresh cookie. The `(auth)` layout therefore runs a silent refresh before rendering anything
session-dependent:

```
mount (auth) layout
   ├─ session.status = 'loading'  → shell skeleton, no page fetches yet
   ├─ POST /auth/refresh (cookie)
   │     ├─ 200 → store token, decode claims, fetch ['me'] + capabilities
   │     │         ├─ activeOrgId present → status 'authenticated' → start SSE hub
   │     │         └─ absent, orgs > 1     → redirect /select-org
   │     └─ 401 → status 'unauthenticated' → redirect /login?next=<path>
```

Blocking page fetches until the session resolves avoids a burst of 401s at startup that would each
trigger the interceptor's refresh path (§10.9). The edge middleware (§5.4) has already redirected
sessionless requests, so this path normally succeeds — it exists to handle an expired or revoked
refresh token, which middleware cannot detect without a round-trip.

**Failure surfaces.** Invalid credentials render inline (never as a toast — the error belongs next to
the field). Login rate limiting (429) disables submit and counts down from `Retry-After`. An expired
invite token renders a dedicated state with a "request a new invitation" action rather than a generic
404, because the user did nothing wrong and needs a path forward.

### 12.2 Dashboard

The role-adaptive landing surface. Its architectural interest is not its content but its **composition
model**: the dashboard is a grid of independent widgets, each owning its own query, loading state,
error boundary, and permission predicate.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Orient the user, surface work needing attention, provide the primary entry points |
| Screens / routes | `/dashboard` |
| Roles | All; content differs entirely by role |
| Backend endpoints | One per widget (recent papers, drafts, approvals depth, quota summary, ingestion status, org activity, platform health) |
| Server state | Per-widget keys, entity-list staleness (30–60 s); quota widget 30 s |
| Client state | None |
| Real-time | `paper.generated`, `paper.approval.changed`, `textbook.parsed`, `quota.warning` → invalidate the affected widget only |
| Key validations | None (read-only surface) |

| Role | Widgets |
|------|---------|
| teacher | Recent papers · Drafts awaiting your action · Quick-start a paper (blueprint or template) · Textbook ingestion status · Your quota usage |
| coaching_admin | Org activity feed · Approvals queue depth and age · Quota and usage against plan · User activity · Recent generations |
| super_admin | Cross-org health · Platform usage trends · Ingestion and generation failure rates · Recent audit highlights |

**Why widget composition.** A dashboard aggregates the most data sources of any screen, which makes it
the surface most likely to have *something* fail. If it were a single query or a single boundary, one
slow analytics rollup would blank the entire landing page — the first thing every user sees. With
independent widgets, a failed widget renders its own retry affordance while the other nine are useful
(§25.5). It also lets each widget carry its own `<Can>` predicate, so the same route serves three roles
without branching page logic.

```
/dashboard
├─ [Recent papers]      query A ──┐
├─ [Drafts]             query B ──┤  parallel, streamed, independent boundaries
├─ [Quota]              query C ──┤  one failure → one retry card, not a blank page
├─ [Ingestion status]   query D ──┤
└─ [Quick start]        static   ──┘
```

Widgets render as server components where they are read-only, each wrapped in its own Suspense boundary
so the shell and the fastest widgets paint immediately while slower ones stream (§5.7, §19.4).

### 12.3 Question Bank

The organization's accumulated question inventory — the asset that makes generation progressively
cheaper and better over time, and the surface teachers spend the most time browsing outside the builder.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Browse, search, filter, inspect, author, edit, tag, retire, and bulk-manage questions; surface provenance and duplicates |
| Screens / routes | `/question-bank` (browse + facets), `/question-bank/[questionId]` (detail/edit drawer or page) |
| Roles | teacher (org scope), coaching_admin, super_admin |
| Backend endpoints | list/search (cursor) · detail · create · update · retire · bulk tag/retire · duplicate check |
| Server state | `[orgId,'questions',{q,filters,sort,cursor}]` search class (15–30 s); `[orgId,'questions',id]` detail (30–60 s); facet metadata long-lived |
| Client state | None persistent; selection set is local to the surface |
| Real-time | `textbook.parsed` (new questions available → invalidate lists), `paper.generated` (AI-authored questions may enter the bank) |
| Key validations | Stem non-empty, option count and correct-answer rules by question type, marks bounds, required taxonomy fields (§16.4) |

**Provenance is always visible.** Every question card and detail view states its origin —
*authored*, *textbook-derived*, or *AI-generated* — along with its source chapter/topic and, for
AI-generated questions, the generation run that produced it. This is not decoration. Teachers are
accountable for what appears on an exam; a question they cannot trace is a question they cannot defend.
Provenance also drives review workflows (AI-generated questions can be filtered for verification) and
is the honest counterpart to an AI product's core risk (§28.5).

**Duplicate detection.** The backend surfaces near-duplicates using the same similarity machinery
described in [06 AI Architecture](06_AI_Architecture.md). The client presents them as a non-blocking
warning at author time ("3 similar questions already exist") with an inline comparison, rather than
refusing the save. Refusing would be wrong: near-duplicates are sometimes intentional (a rephrased
variant for a different difficulty). The user decides; the system informs.

**Scale.** The bank grows without bound — tens of thousands of questions for an established
organization. Every read is server-paginated with cursors, the browse list is virtualized (§19.10), and
no operation loads the full collection. Bulk actions operate on a *filter*, not on a client-held id
list, so "retire all questions from this retired textbook" is one request rather than ten thousand
(§18.15).

### 12.4 Textbook Upload (Ingestion)

The entry point for source material. The pipeline itself belongs to
[05 Textbook Ingestion](05_Textbook_Ingestion.md); the upload mechanics belong to §17. This module owns
the *representation of a long, multi-stage, partially-failable process*.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Upload source files, show ingestion progress by stage, review chapter/topic mapping, handle partial success, re-run failed stages |
| Screens / routes | `/textbooks` (library + upload), `/textbooks/[textbookId]` (detail: stages, extracted structure, warnings) |
| Roles | teacher, coaching_admin, super_admin |
| Backend endpoints | init upload · complete upload · list · detail · re-run stage · update chapter mapping · delete |
| Server state | `[orgId,'textbooks']` list, `[orgId,'textbooks',id]` detail — entity class, patched by progress events |
| Client state | `uploads` store (queue, byte progress, abort handles — §17.9) |
| Real-time | `textbook.progress` (patch), `textbook.parsed` (invalidate), `textbook.failed` (invalidate + failure notification) |
| Key validations | File type, size ceiling, page-count limit — all advisory, server re-validates (§16.8, §17.10) |

**Two progress phases, never conflated.** The UI shows *upload* (byte progress against the signed PUT)
and *processing* (OCR → parse → structure → index, driven by `textbook.progress`) as visually distinct
stages:

```
[ Upload ██████████ 100% ]  →  [ Processing ]
                                 ├─ OCR            ████████░░  82%   (page 41/50)
                                 ├─ Parse          ░░░░░░░░░░  queued
                                 ├─ Structure map  ░░░░░░░░░░  queued
                                 └─ Index          ░░░░░░░░░░  queued
```

Merging them into a single bar would be actively harmful: a stalled OCR job would be indistinguishable
from a stalled network upload, and the user's correct response differs completely between the two
(retry the upload vs. wait or report the job). Honest representation of asynchronous state is a
principle here (§2.3, §2.10), not a stylistic choice.

**Partial success is a first-class outcome.** A 400-page scanned textbook may yield clean text for 380
pages and unusable output for 20. `textbook.parsed` carries warnings, and the detail view presents the
result as *usable with caveats*: what was extracted, what was skipped and why, and per-stage re-run
actions. Treating partial success as failure would discard genuinely useful work; treating it as full
success would silently produce a question bank with gaps the teacher never learns about.

### 12.5 Paper Builder

The module where a blueprint becomes a reviewed, finalized paper. It owns `/papers`,
`/papers/[paperId]`, and `/papers/[paperId]/build`, the paper list and detail surfaces, the blueprint
and section builders, question cards, generation and regeneration control, live preview, versioning,
autosave, and conflict resolution. It is the only module holding a rich client-side editor buffer — the
one sanctioned exception to minimal client state (§2.15, §9.4).

Because it is the product's centre of gravity and by far the most architecturally involved surface, it
is specified separately and in full: **see §13**.

### 12.6 Templates

A **template** is a reusable blueprint: the structural recipe for a paper (sections, question types and
counts, marks, difficulty and Bloom targets, instructions) without any actual questions. A
**blueprint** is the concrete configuration attached to one specific paper. Applying a template
*copies* it into a paper's blueprint.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Browse org templates, create from scratch or from an existing paper, edit, version, share within the org, apply to a new paper |
| Screens / routes | `/admin/templates` (manage); template picker embedded in the builder's blueprint step |
| Roles | teacher: browse and apply · coaching_admin/super_admin: full management |
| Backend endpoints | list · detail · create · create-from-paper · update (new version) · archive · apply |
| Server state | `[orgId,'templates']`, `[orgId,'templates',id]` — entity class; templates change rarely so lists tolerate longer staleness |
| Client state | None |
| Real-time | None |
| Key validations | Name uniqueness within the org (async, debounced), structural completeness, program compatibility on apply (§16.9) |

**Copy, not reference.** When a template is applied, its structure is copied into the paper's
blueprint. A later edit to the template therefore does **not** alter existing papers. This is the right
default for an assessment product: a paper that silently restructured itself after a template edit
would be a correctness disaster, potentially after approval. The trade-off is that fixing a mistake in
a template does not propagate; the UI compensates by showing, on the template detail, how many papers
were built from each version, and offering an explicit "apply the new version to these drafts" action —
opt-in, scoped to drafts, never touching approved papers.

**Program compatibility.** Applying a template built for one program to a paper in another may
reference chapters that do not exist there. The client checks compatibility before applying and
presents what will not carry over, so the user is not surprised by half-empty sections after the fact.

### 12.7 Approval

The control that makes generated papers institutionally trustworthy. Its defining constraint is
separation of duties (BR-6): the author of a paper cannot be its sole approver.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Approvals queue, review surface, approve/reject with comments, request changes, approval history and audit trail |
| Screens / routes | `/admin/approvals` (queue), review opens the paper in a read-only review mode |
| Roles | coaching_admin, super_admin (teachers see status on their own papers, cannot approve) |
| Backend endpoints | queue list · paper review detail · approve · reject · request-changes · history |
| Server state | `[orgId,'approvals',{filters,page}]`, `[orgId,'papers',id]`, `[orgId,'papers',id,'approval-history']` |
| Client state | None |
| Real-time | `paper.approval.changed` → invalidate paper detail, queue, and dashboard widgets |
| Key validations | Comment required on reject and on request-changes; eligibility preconditions are projected, not decided (§16.9) |

**Paper state machine as the UI understands it:**

```
        ┌────────┐  submit for approval   ┌──────────────────┐
        │ draft  │───────────────────────▶│ pending_approval │
        └────────┘                        └────────┬─────────┘
             ▲                                     │
             │ author revises                      ├── approve ────▶ ┌──────────┐
             │                                     │                  │ approved │
   ┌─────────┴──────────┐  request changes         │                  └────┬─────┘
   │ changes_requested  │◀────────────────────────┤                       │ new version
   └────────────────────┘                          │                       ▼
                              ┌──────────┐         │                  ┌────────┐
                              │ rejected │◀── reject                   │ draft  │
                              └──────────┘                             └────────┘
```

**Projection, not decision.** The client disables *Approve* when the current user authored the paper,
with a tooltip stating the reason — "You can't approve a paper you authored" — following the "hide by
role, disable by state" rule (§7.5). The capability exists for this role, so hiding it would be
confusing; the *instance* is blocked, so disabling with an explanation is correct.

But the client never *decides* eligibility. BR-6 is enforced server-side, along with rules the client
cannot know: whether another approver already acted, whether the paper changed since it was queued,
whether the approver's role was revoked moments ago. A `403` or `409` from the approve action is a
normal, expected outcome, handled by re-syncing the paper and explaining what changed (§10.12, §25.6) —
not an error state to be avoided by cleverer client logic.

**Concurrent approval.** Two admins can open the same queued paper. The second to act receives a `409`
and sees "this paper was already approved by <name>" with the queue refreshed. This is why the queue
carries a version token and why approve is never optimistic (§9.8).

### 12.8 Analytics

Read-only insight over daily rollups. No mutation surface exists in this module at all, which
simplifies it considerably.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Usage and generation volume, question quality signals, difficulty and Bloom distribution across produced papers, approval turnaround, per-teacher and per-program breakdowns; platform-wide equivalents for super_admin |
| Screens / routes | `/admin/analytics` (org), `/platform/analytics` (cross-org) |
| Roles | coaching_admin (own org), super_admin (own org + platform) |
| Backend endpoints | rollup queries by metric, date range, and grouping · report export |
| Server state | `[orgId,'analytics',metric,{from,to,granularity,groupBy}]` — rollup class, 15–30 min staleTime, no refetch on focus |
| Client state | Date range and grouping mirrored in the URL (§18.5) |
| Real-time | None — rollups update daily; a live feed would imply a precision the data does not have |
| Key validations | Date range bounds and ordering; maximum span per granularity |

**Why long staleness and no focus refetch.** The underlying data is a daily rollup
([03 Database Design](03_Database_Design.md)). Refetching every time the tab regains focus would issue
expensive aggregate queries to return byte-identical results. Worse, it would imply real-time accuracy
to the user. Long staleness is both a performance decision and an honesty decision.

**Timezone policy.** Rollups are computed in the organization's configured timezone, and every date
range and axis label is rendered in that timezone — never the browser's. A coaching admin in a
different timezone from their institution must see the institution's days, or "yesterday's generation
count" becomes meaningless. The timezone in force is always labelled on the surface.

**Charts ship a table.** Every chart has an accessible data-table alternative (§21.13), which doubles as
the export path. Empty ranges render a distinct "no activity in this period" state rather than an empty
chart frame, because an empty chart reads as a loading failure.

### 12.9 Billing

Plan, quota, and usage. Architecturally its most important property is that **quota state is visible
before expensive work starts**, not after it fails.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Plan and entitlements, usage meters against limits, invoice history, plan changes, quota warnings |
| Screens / routes | `/admin/billing`; quota meter surfaces in the shell and the builder |
| Roles | coaching_admin, super_admin manage; teacher sees a read-only usage indicator |
| Backend endpoints | plan/entitlements · usage summary · invoice list · plan change |
| Server state | `[orgId,'quota']` 30 s staleness, refetch on focus; `[orgId,'billing',…]` entity class |
| Client state | None |
| Real-time | `quota.warning` → invalidate quota, escalate the banner |
| Key validations | Plan-change preconditions are server-decided; the client projects affordability and shows the consequence |

**Quota before commitment.** Generation and ingestion consume metered capacity. The builder shows the
projected cost of a generation *before* the user submits it, and the quota meter is fetched fresh at
that moment rather than served from a long cache — a user who starts a 50-question generation and is
told mid-job that they ran out has lost time and trust. Warnings escalate: informational at a soft
threshold, prominent as the limit approaches, and blocking with an upgrade path at the hard limit
(§23.8).

Plan changes are never optimistic (§9.8) — money and entitlements are the definition of consequential.

### 12.10 Settings

Configuration split cleanly along a single axis: **who does this affect?**

| Scope | Contents | Who may edit | Route |
|-------|----------|--------------|-------|
| User | Theme, density, notification preferences, language | The user | `/settings`, `/profile` |
| Organization | Branding (logo, accent — §22.12), academic structure, paper defaults, approval policy | coaching_admin | `/settings` (org tab) |
| Org membership | Users, roles, invitations, deactivation | coaching_admin | `/admin/users` |
| Curriculum | Programs, classes, subjects, chapter trees | coaching_admin | `/admin/programs` |
| Platform | Organizations, prompt versions, audit log | super_admin | `/platform/*` |

| Aspect | Detail |
|--------|--------|
| Server state | `[orgId,'settings']`, `[orgId,'users',…]`, `[orgId,'programs',…]` entity class; curriculum reference data long-lived |
| Client state | `ui` store for the preferences that must apply before a round-trip (theme, density) |
| Real-time | None |
| Key validations | Branding contrast constraints (§22.12), curriculum structural integrity, role-change guardrails (cannot remove the last admin) |

The user/org split matters because it determines *where* a preference is persisted: user preferences
that should follow someone across devices go to the server; device-local ones stay in `localStorage`
(§9.9). Getting this wrong produces the familiar annoyance of settings that mysteriously differ between
a laptop and a desktop.

Curriculum management deserves a note: editing a chapter tree affects existing questions, textbooks,
and blueprints that reference it. The UI surfaces the blast radius before a destructive structural
change ("42 questions reference this chapter") rather than discovering it afterwards.

### 12.11 Notifications

The in-app record of things that happened while the user was or was not watching. Transport is §11;
presentation is §23. This module owns the **centre** and the **read model**.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Notification centre, unread badge, mark read/all read, filtering by type, history pagination |
| Screens / routes | `/notifications`; bell + dropdown in the header (§8.3) |
| Roles | All |
| Backend endpoints | list (cursor) · mark read · mark all read · unread count |
| Server state | `[orgId,'notifications',{filters,cursor}]`, `[orgId,'notifications','unread-count']` |
| Client state | `toasts` store (transient presentation only, never history) |
| Real-time | `notification.created` → patch (payload is patch-grade), increment badge |
| Key validations | None |

The separation between the `toasts` store and the notification cache is deliberate and worth stating: a
toast is an ephemeral *presentation* of an event, while a notification is a *persisted record*. One
event may produce both, either, or neither. Keeping history in the query cache means it is paginated,
invalidatable, and consistent across tabs; keeping toasts in a store means they are cheap and vanish
with the tab. Conflating them would either persist toasts (noise) or lose history (data loss).

Notification types use the `NT-` family from [01 Product Requirements](01_Product_Requirements.md) as
the shared vocabulary between backend and client, so a new backend notification type renders sensibly
without a client change.

### 12.12 Profile

Personal identity and account security.

| Aspect | Detail |
|--------|--------|
| Responsibilities | Name and contact details, password change, active sessions/devices with revoke, organization memberships and default org, accessibility and display preferences |
| Screens / routes | `/profile` |
| Roles | All (self only) |
| Backend endpoints | get/update profile · change password · list sessions · revoke session · set default org |
| Server state | `['me']`, `['me','sessions']` — org-independent keys |
| Client state | `ui` store for display preferences applied immediately |
| Real-time | None |
| Key validations | Password policy, current-password confirmation, email format; uniqueness is server-checked |

**Session management is a security surface.** Listing active sessions (device, approximate location,
last seen) and allowing revocation is the user-facing half of the refresh-token session families
described in §6.1. Revoking a session invalidates its refresh family server-side; revoking *this*
session is equivalent to logout and runs the full teardown (§9.10). A password change revokes all other
sessions by default, with the reasoning stated in the UI rather than assumed.

Profile queries are keyed `['me']` — **not** org-first — because identity spans organizations. This is
the documented exception to the org-first key rule in §9.5, and it exists precisely so that the org
switch cache reset (§9.7) does not needlessly discard identity data.

### 12.13 Cross-Module Coupling

Modules do not import each other, but they do share cache entries — and that sharing is where change
ripples. This table is the practical companion to the invalidation matrix in §9.6:

| Shared data | Written by | Read by | Ripple |
|-------------|-----------|---------|--------|
| `[orgId,'papers',…]` | Paper Builder, Approval | Dashboard, Analytics, Notifications | Approval action invalidates paper detail, queue, and two dashboard widgets |
| `[orgId,'questions',…]` | Question Bank, Textbooks (via ingestion), Paper Builder (via generation) | Paper Builder, Analytics | `textbook.parsed` invalidates bank lists and the ingestion widget |
| `[orgId,'quota']` | Billing | Paper Builder, Textbooks, Dashboard, shell meter | Any metered job completion invalidates quota everywhere it is shown |
| `[orgId,'templates',…]` | Templates | Paper Builder (picker) | Template edits refresh the builder's picker without coupling the modules |
| Curriculum reference data | Settings | Question Bank, Textbooks, Paper Builder, Analytics | Long-lived cache; a curriculum edit invalidates broadly and is therefore rate-limited by design |
| `['me']`, capabilities | Auth, Profile | Every module via `useCan()` | Role change re-mints the token and refetches capabilities (§7.2) |

Shared *components* are a separate axis: `DataTable`, `FilterBar`, `EmptyState`, `StatusBadge`,
`ProgressIndicator`, `ConfirmDialog`, and `PageHeader` are used by nearly every module and live in
`components/shared` (§14.6), owned by no feature.

### 12.14 Module Loading Strategy

| Loading | Modules | Reason |
|---------|---------|--------|
| Eager (in the shell bundle) | Auth (session + refresh), Notifications (hub + bell), Profile (menu) | Present on every authenticated screen; lazy-loading them would delay the shell (§8.8) |
| Lazy per route | Dashboard, Question Bank, Textbooks, Templates, Approval, Analytics, Billing, Settings | Route-level code splitting is free from the App Router; a teacher never downloads admin code |
| Lazy + heavy-leaf split | Paper Builder | Route-split, plus dynamic imports for the editor, drag-and-drop, and preview renderer (§19.5) |
| Lazy, role-gated | Platform surfaces | super_admin-only code is not shipped to the other 99% of users |

Role-gated lazy loading has a modest security benefit — it does not put admin code in a teacher's
bundle — but the honest justification is bundle size, not secrecy. Nothing in the client is secret
(§24.13); the enforcement is server-side regardless.

### 12.15 Feature Module Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Organization | By feature, with `index.ts` as the only public surface | Code that changes together lives together; parallel work without collisions (§4.4) | Requires lint enforcement to stay real |
| Inter-module communication | Query cache + shared components only | Removes the import graph as a coupling surface; invalidation is the explicit contract | Coupling moves into the invalidation matrix, which must be maintained |
| Dashboard | Independent widgets, independent boundaries | The most failure-prone surface degrades gracefully; one route serves three roles | More boundaries and queries to manage |
| Analytics | Read-only, long staleness, no real-time | Rollup-backed data cannot be fresher than daily; implying otherwise is dishonest | Users must understand the data is not live (labelled) |
| Templates | Copy on apply, never reference | An approved paper can never restructure itself | Template fixes do not propagate; explicit opt-in re-apply provided |
| Approval eligibility | Projected client-side, decided server-side | BR-6 and concurrent-approval races are only knowable at the server | 403/409 on approve is a normal path to design for, not an edge case |
| Ingestion progress | Upload and processing shown as separate phases | The user's correct response differs between the two failure modes | Slightly more complex progress UI |
| Quota | Fetched fresh before expensive work | Prevents starting work that cannot finish | An extra request at the moment of commitment |
| Profile keys | `['me']`, not org-first | Identity spans organizations; survives the org-switch reset | One documented exception to the key convention |
| Paper Builder | Extracted to its own section | Its complexity is an order of magnitude above every other module | A second place to look for paper-related behaviour |

---

## 13. Paper Builder Architecture

### 13.1 Purpose and Design Goals

The Paper Builder is where the product's value is actually realized: a teacher declares what they want
(a blueprint), the system produces a candidate paper (retrieval → AI gap-fill → assembly), and the
teacher reviews, edits, regenerates, and finalizes it. Everything else in the platform exists to feed or
consume this surface.

Five goals shape its architecture:

| Goal | What it demands |
|------|-----------------|
| The teacher stays in control | Every AI output is inspectable, editable, replaceable, and lockable. Nothing is applied without the user seeing it. |
| Editing feels immediate | Typing, reordering, and toggling must respond within a frame, on a paper with 60+ questions, on a mid-range laptop. |
| Asynchronous work is represented honestly | Generation takes minutes and can partially fail. The UI never shows a state the server has not confirmed. |
| Unsaved work is never lost | Autosave, draft recovery, and unsaved-change guards, with a save indicator the user can trust. |
| Concurrent change is handled, not ignored | Two tabs, two teachers, or a worker writing generated questions must not silently overwrite each other. |

These goals are in tension with §2.7 (minimal client state). The builder is the one place where the
tension is resolved in favour of a rich local model, and §13.2 explains exactly how far that exception
extends.

### 13.2 Three-Pane Model

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ ◀ Papers   Class X Mathematics — Term 1        ● Saved 12:04    [Preview] [Submit ▾]  │  ← condensed header
├──────────────────┬───────────────────────────────────────────────┬─────────────────────┤
│ BLUEPRINT        │ CANVAS                                        │ LIVE PREVIEW        │
│ (config)         │ (sections + question cards)                   │ (exam layout)       │
│                  │                                               │                     │
│ Program  ▾       │ ┌─ Section A · MCQ · 1 mark × 10 ─────────┐   │  ┌───────────────┐  │
│ Class    ▾       │ │ ⣿ 1. What is the value of …      🔒 ✎ ↻ │   │  │  ORG HEADER   │  │
│ Chapters ☑☑☐     │ │ ⣿ 2. Which of the following …       ✎ ↻ │   │  │  Class X Math │  │
│                  │ │ ⣿ 3. ⟳ generating…                      │   │  │  Time: 3h     │  │
│ Total marks  80  │ │ ⣿ 4. [empty slot]                       │   │  │  Marks: 80    │  │
│ Duration   180m  │ └──────────────────────────────────────────┘   │  ├───────────────┤  │
│                  │ ┌─ Section B · Short · 3 marks × 5 ───────┐   │  │ SECTION A     │  │
│ Difficulty       │ │ ⣿ 1. Explain why …               🔒 ✎ ↻ │   │  │ 1. What is …  │  │
│  E ███░ 30/30    │ │ ⣿ 2. Derive the …                   ✎ ↻ │   │  │ 2. Which of … │  │
│  M █████ 50/50   │ └──────────────────────────────────────────┘   │  │ …             │  │
│  H ██░░ 20/25 ⚠  │                                               │  └───────────────┘  │
│                  │ [+ Add section]                               │  ○ Answer key       │
│ Bloom            │                                               │  Page 1 of 3        │
│  ▁▃▅▂▁▁ (advis.) │                                               │                     │
│                  │                                               │                     │
│ Marks  80/80 ✓   │                                               │                     │
│ [Generate]       │                                               │                     │
└──────────────────┴───────────────────────────────────────────────┴─────────────────────┘
   left: declares      middle: the document being edited            right: derived view
```

The three panes map onto three distinct mental activities — *specify*, *edit*, *verify* — and each pane
has a different relationship to the data. The left pane writes the blueprint. The middle pane writes the
document. The right pane is **purely derived** and writes nothing (§13.13). Keeping "declare", "edit",
and "verify" simultaneously visible is what lets a teacher trust the output: they can see the target,
the content, and the result at once.

The builder uses the **focused layout variant** (§8.9): the sidebar collapses to an icon rail and the
header condenses, because three panes need horizontal room. On tablet the preview collapses to an
on-demand overlay; on phones the builder becomes a review-and-approve surface rather than a cramped
editor (§20.4).

### 13.3 The Document Model

The builder holds an **editor buffer**: an in-memory, ordered tree of addressable nodes.

```
PaperDraft
  paperId, version, revisionId, status, title, language, instructions
  blueprint { programId, classId, chapterIds[], totalMarks, durationMinutes,
              difficultyTargets{}, bloomTargets{}, sectionSpecs[] }
  sections[ { sectionId, order, title, questionType, marksPerQuestion,
              choiceGroup{ attemptCount, availableCount } | null,
              slots[ { slotId, order,
                       state, origin, locked,
                       questionId | null, marks, difficulty, bloomLevel,
                       chapterId, topicId, edited } ] } ]
  meta { dirtyPaths[], lastSavedAt, saveState }
```

Three properties of this shape do real architectural work:

1. **Every node is addressable.** `sectionId` and `slotId` give a stable path to any part of the
   document, which is what makes patch-granular autosave (§13.15), invertible undo commands (§13.14),
   and subtree-level conflict merging (§13.16) possible. A flat array of questions would make all three
   substantially harder.
2. **Order is explicit and sparse.** `order` is a fractional/sparse index, not an array position, so
   moving one question rewrites one field rather than renumbering the list (§13.6).
3. **Slots exist before questions do.** A blueprint declares slots; generation fills them. This is what
   lets the canvas render the paper's full structure immediately at generation time and materialize
   questions into it as they arrive, rather than showing an empty screen for two minutes.

**The buffer/server boundary.** The buffer is a *working copy* of the server document, not a second
source of truth:

| Concern | Buffer owns | Server owns |
|---------|-------------|-------------|
| In-progress edits, selection, undo stacks, dirty paths, save state | Yes | No |
| Canonical paper content, versions, approval state, locks-of-record | No | Yes |
| Generated question content | Materialized into the buffer as events arrive | Authoritative |
| Marks/distribution arithmetic for feedback | Computed locally | Recomputed and authoritative at submit (§13.12) |

The buffer never becomes authoritative. Every change flows to the server through autosave and the
server's response is what settles the record (§13.15). This is the same discipline as optimistic updates
(§9.8), applied at document scale.

### 13.4 Blueprint Builder

The blueprint declares what the paper should be, before any question exists.

| Group | Fields |
|-------|--------|
| Curriculum | Program, class/grade, subject, chapters and topics (from cached curriculum reference data) |
| Shape | Total marks, duration, language, general instructions |
| Structure | Ordered section specs: title, question type, marks per question, question count, optional choice group |
| Targets | Difficulty mix (easy/medium/hard percentages), Bloom level mix |

**Progressive disclosure.** A teacher under time pressure should be able to produce a reasonable paper
from four decisions: program, class, chapters, and total marks. Everything else has a defensible default
(derived from org settings or the last paper) and lives behind *Advanced*. Coaching admins get the
advanced controls expanded by default and additionally control the org-wide defaults (§12.10). The same
form serves both — the disclosure differs, not the schema (§15.10).

**Template application.** Applying a template copies its structure into the blueprint (§12.6). The
client shows a diff-style summary of what will change before applying, because silently replacing a
blueprint the user has already customized would be destructive.

**Live feasibility feedback.** The most valuable thing the blueprint form can tell a user is *whether
this paper can actually be produced*. Only the server knows: it must count matching questions in the
bank per chapter/type/difficulty and judge whether AI gap-fill can cover the remainder given the
available source material.

```
blueprint field changes
   └─ debounce ~600ms, coalesce, cancel superseded (AbortController)
        └─ POST feasibility check  (idempotent read-shaped call)
             ├─ feasible          → quiet ✓ with projected bank/AI split
             ├─ feasible with gaps→ inline advisory: "Section C: 3 of 8 will be AI-generated"
             └─ infeasible        → blocking explanation + the specific constraint to relax
```

The debounce and cancellation matter: without them, dragging a marks slider would fire a stream of
expensive server-side counts and hit the rate limiter (§10.12). Feasibility is advisory in the UI but
authoritative at generation time — the server re-checks, and a `400` business-rule response is a normal
outcome (§16.3).

### 13.5 Section Builder

Sections are the paper's structural units. Each carries constraints that apply to every slot inside it.

| Operation | Behaviour | Constraint |
|-----------|-----------|-----------|
| Add section | Appends with defaults inherited from the previous section | Total marks recomputed immediately |
| Rename | Inline edit | Non-empty |
| Reorder | Drag or keyboard (§13.6) | Order is presentational only; no content impact |
| Delete | Confirm dialog naming what will be lost | Blocked if it would drop the paper below one section |
| Change question type | Warns that incompatible slots will be cleared | Existing questions of another type cannot remain |
| Change marks per question | Recomputes section and paper totals | Must keep the paper total reachable |
| Set choice group | "Attempt any N of M" | N < M, and marks arithmetic switches to the choice rule below |

**Choice groups change the arithmetic.** In a section of 7 questions where students attempt any 5 at 3
marks each, the section contributes **15** marks (5 × 3), not 21. Every marks calculation in the builder
— section subtotal, paper total, distribution denominators — must use the *attempted* count, while
question-count validation uses the *available* count. Getting this backwards produces a paper whose
printed total does not match its stated total, which is the single most consequential arithmetic error
this product can make. It is therefore evaluated in one shared utility, unit-tested exhaustively
(§27.4), and re-verified server-side at submit.

### 13.6 Question Cards

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ⣿  3.  [AI-generated]  Ch 4 · Quadratic Equations           🔒  ✎  ↻  ⋮ │
│                                                                          │
│     Find the roots of x² − 5x + 6 = 0 and verify by substitution.        │
│                                                                          │
│     (a) 2, 3     (b) −2, −3     (c) 1, 6     (d) −1, −6                 │
│     ✓ (a)                                                                │
│                                                                          │
│     3 marks   ·   Medium   ·   Apply   ·   ⚠ similar to Q-1042           │
└──────────────────────────────────────────────────────────────────────────┘
   ⣿ drag handle   🔒 lock   ✎ edit   ↻ regenerate   ⋮ more (replace, remove, flag)
```

The card carries the stem, options/answer, marks, difficulty, Bloom level, chapter/topic, provenance,
lock state, status badges, and per-card actions. Provenance is always shown (§12.3): a teacher must be
able to see at a glance which questions came from the AI and therefore warrant closer reading.

**Card state machine:**

```
                   ┌────────────┐  generation targets this slot   ┌──────────────┐
                   │ empty slot │───────────────────────────────▶ │  generating  │
                   └────────────┘                                 └───┬──────┬───┘
                          ▲                                           │      │ failed
                          │ user clears                    ready      │      ▼
                          │                                           │  ┌────────┐
                          │                                           │  │ failed │──retry──┐
                    ┌─────┴──────┐        user edits            ┌─────▼──┴──────┐  │        │
                    │  replaced  │◀──── accept candidate ───────│    filled     │◀─┴────────┘
                    └────────────┘                              └───┬───┬───┬───┘
                                                                    │   │   │
                                          user edits ───────────────▶│   │   │◀─── user flags
                                                  ┌──────────┐       │   │   │      ┌─────────┐
                                                  │  edited  │◀──────┘   │   └─────▶│ flagged │
                                                  └──────────┘           │          └─────────┘
                                                       ┌────────┐        │
                                                       │ locked │◀───────┘ user locks
                                                       └────────┘  (orthogonal: any filled
                                                                     state may also be locked)
```

`locked` is deliberately drawn as orthogonal rather than as a peer state: a question can be filled *and*
locked, edited *and* locked. Modelling lock as a boolean flag on the slot rather than a state avoids a
combinatorial state explosion.

**Inline editing vs. detail drawer.** Light edits (marks, difficulty, a typo in the stem) happen inline
on the card, because switching context for a two-character fix is friction. Substantive edits (rewriting
options, changing question type, adding an explanation, viewing full provenance and similar questions)
open a detail drawer, because they need space and their own validation surface. The rule: inline for
single-field changes with obvious validity, drawer for anything multi-field or structural.

### 13.7 Drag and Drop

| Move | Allowed? | Reason |
|------|---------|--------|
| Reorder within a section | Yes | Pure presentation; no constraint interaction |
| Move across sections, compatible type and marks | Yes | Both sections accept the question as-is |
| Move across sections, incompatible question type | **No** | An MCQ cannot live in a "short answer, 3 marks" section; the drop target refuses with an explanation rather than silently converting |
| Move across sections, different marks per question | Yes, with the marks rewritten to the target section's value and a warning | The section's spec wins; the user is told the marks changed |
| Move a locked question | Yes | A lock protects content against regeneration, not position |
| Reorder sections | Yes | — |
| Drop into a full section | Yes; the section count grows and the blueprint delta is flagged | Blocking would be more annoying than surfacing the mismatch |

Drop targets are explicit: valid targets highlight, invalid targets show a refusal cue with the reason
on hover/focus. Silently rejecting a drop teaches the user nothing.

**Optimistic reorder.** Reordering is the archetypal safe, reversible, high-frequency action, so it is
optimistic (§9.8): the card moves immediately, a persist call follows, and a failure restores the prior
order with an explanation. Waiting for a round-trip on every drag would make the canvas feel broken.

**Sparse ordering.** `order` values are spaced (e.g. 100, 200, 300), so inserting between two items
writes a single value (150) rather than renumbering everything after it. This matters for both
performance (one field patch instead of N) and conflict behaviour (two users reordering different parts
of the paper touch disjoint fields and merge cleanly — §13.16). Periodic renormalization runs when gaps
get too tight.

**Keyboard reordering is mandatory, not a fallback.** Drag-and-drop that only works with a mouse would
make the builder's core interaction inaccessible (§21.9): pick up with Space/Enter on the handle, move
with arrows, drop with Space/Enter, cancel with Escape — with each move announced ("Question 3 of 10,
Section A, moved to position 2"). This is also the only usable reorder mechanism on touch for long
papers, where dragging across a scrolling viewport is miserable.

### 13.8 Question Locking

A **lock** means: *this question is settled — regeneration must not touch it.*

| Property | Behaviour |
|----------|-----------|
| Granularity | Per question, and per section (locks every slot in it) |
| Effect on regeneration | Locked slots are excluded from the request and preserved verbatim in the result (§13.9) |
| Effect on editing | None — a lock is not a read-only flag; the user can still edit a locked question |
| Effect on reordering | None (§13.7) |
| Visibility | Lock badge on the card, count in the section header, and a summary before any regeneration |
| Persistence | Part of the document; survives save, reload, and version creation |

The lock exists because whole-section or whole-paper regeneration is otherwise unusable: a teacher who
has hand-perfected three questions out of ten will not risk a regeneration that might discard them.
Locking converts regeneration from an all-or-nothing gamble into a targeted tool.

**Concurrency.** Locks are part of the saved document, so two clients can disagree about them.
Lock/unlock is a single-field change on an addressable node, which merges cleanly under the rules in
§13.16 — but a lock set *after* a regeneration request was submitted cannot retroactively protect that
question. The UI therefore snapshots and displays the lock set at submission time, so the user knows
exactly what was protected.

### 13.9 Regeneration

Three scopes, deliberately escalating in cost and friction:

| Scope | Trigger | Cost | Guardrail |
|-------|---------|------|-----------|
| Single question | `↻` on a card | 1 unit | None beyond quota check; this is the common case |
| Section | Section menu | N units | Confirm dialog naming how many will be replaced and how many are locked |
| Whole paper | Blueprint pane | All unlocked slots | Confirm dialog + explicit typed acknowledgement when the count is large; shows the quota impact |

**Request shape.** A regeneration request carries the constraints the AI must honour: the blueprint
context, the target slots, the **locked** questions (as content to preserve and to avoid duplicating),
the **excluded** question ids (previously rejected candidates and everything already in the paper, so
the AI does not re-propose them), and the per-slot targets (type, marks, difficulty, Bloom, chapter).
Sending exclusions is what stops the frustrating loop of the AI offering the same rejected question
again.

```
 Builder                    API                     Worker                  AI Provider
    │  ↻ regenerate slot     │                        │                          │
    ├───────────────────────▶│ validate + quota check │                          │
    │◀── 202 { jobId } ──────┤────── enqueue ────────▶│                          │
    │  card → 'generating'   │                        │  retrieval (bank)        │
    │                        │                        ├─────────────────────────▶│ gap-fill
    │◀─ paper.generation.progress (patch) ────────────┤                          │
    │◀─ paper.question.ready { slotId, candidate } ───┤◀─────────────────────────┤
    │  card → candidate shown beside the current one  │                          │
    │                                                 │                          │
    │  user: [Accept]  → replace slot content, buffer dirty → autosave (§13.15)   │
    │  user: [Reject]  → keep current, add candidate to the exclusion list        │
    │  user: [↻ again]  → new request with the enlarged exclusion list             │
```

**Accept/reject with comparison.** A regenerated question is a *candidate*, not a replacement. The card
shows old and new side by side (or stacked on narrow viewports) with their differing attributes
highlighted, and nothing changes until the user accepts. This follows directly from §2.9: replacing a
question the teacher had reviewed, without asking, would be an unacceptable AI behaviour.

**Failure and partial results.** A section regeneration that fills six of eight slots reports partial
success: the six candidates are offered, the two failures are marked with a retry action, and the
original questions remain in place. Nothing is destroyed by a failed regeneration — the buffer's prior
content is only replaced on accept.

**Quota awareness.** Every regeneration control shows its projected cost, and the quota is refetched
before a section- or paper-scope regeneration (§12.9). A user should never discover mid-job that they
had capacity for three of eight questions.

### 13.10 Difficulty Distribution

The blueprint declares a target mix; the paper has an actual mix. The builder's job is to make the delta
visible and actionable without becoming a nag.

```
Difficulty          target        actual
  Easy      ███████░░░  30%   →  ██████░░░░  27%    ✓ within tolerance
  Medium    ████████░░  50%   →  █████████░  55%    ✓
  Hard      ████░░░░░░  20%   →  ███░░░░░░░  18%    ✓
                                                     ▲ target marker
```

Distribution bars render the actual mix with the target as an overlaid marker, so "am I on target?" is
answered by shape rather than by arithmetic. Recalculation triggers on any change to a slot's
difficulty, marks, count, or lock state, and on any blueprint target change — computed as derived state
from the buffer (§9.11), memoized at the section boundary so a single-field edit does not recompute the
whole paper (§13.19).

**Guide, don't block.** A distribution outside tolerance is a **warning**, never a submission blocker.
An exam that is 5% heavier on hard questions than intended is a pedagogical judgement, not an error, and
a teacher who has deliberately chosen it should not be forced to fight the tool. The builder instead
offers a constructive action: "Replace 2 medium questions with hard ones?" which pre-fills a targeted
regeneration.

### 13.11 Bloom Distribution

Bloom's taxonomy levels (Remember, Understand, Apply, Analyse, Evaluate, Create) get the same
visualization treatment, with one deliberate difference: **Bloom targets are advisory only**.

The reason is honesty about classification confidence. Difficulty is a relatively stable property that
the bank records and teachers reliably agree on. A question's Bloom level is an interpretive judgement —
AI-assigned for generated questions, and genuinely debatable for many items. Enforcing a hard constraint
on an uncertain classification would produce false precision: the tool would block a perfectly good
paper on the basis of a label it is not confident about.

The UI states this explicitly ("Bloom targets are advisory") rather than leaving the user to infer why
one distribution warns and the other blocks. Where a Bloom level was AI-assigned, the card exposes that
provenance so the teacher can correct it.

### 13.12 Marks Validation

The complete arithmetic, its severities, and where each rule is evaluated:

| Rule | Scope | Severity | Evaluated | Message intent |
|------|-------|----------|-----------|----------------|
| Question marks within the section's `marksPerQuestion` | Slot | Error | Client + server | "Questions in this section are worth 3 marks" |
| Question marks > 0 | Slot | Error | Client + server | "Marks must be greater than zero" |
| Marks granularity (integer or half-mark per org policy) | Slot | Error | Client + server | "Use whole or half marks" |
| Section subtotal = count × marksPerQuestion | Section | Error | Client + server | "Section total doesn't match its questions" |
| Choice-group subtotal = attemptCount × marksPerQuestion | Section | Error | Client + server | "Attempt-any sections count only the attempted questions" |
| Choice group: attemptCount < availableCount | Section | Error | Client + server | "Students must have more questions than they attempt" |
| Paper total = Σ section subtotals | Paper | Error | Client + server | "Paper total is 78 of 80 declared marks" |
| Paper total = blueprint `totalMarks` | Paper | Error | Client + server | "Adjust questions or update the declared total" |
| Every slot filled | Paper | Error at submit | Client + server | "3 questions are still empty" |
| No slot in `generating` or `failed` state | Paper | Error at submit | Client | "Wait for generation to finish, or retry the failed questions" |
| Duration plausible for the marks total | Paper | Warning | Client | "3 hours for 80 marks is unusual for this program" |
| Difficulty mix within tolerance | Paper | Warning | Client | §13.10 |
| Bloom mix within tolerance | Paper | Warning (advisory) | Client | §13.11 |
| Duplicate or near-duplicate questions in one paper | Paper | Warning | Server | "Q4 and Q11 are very similar" |

**Errors block submission; warnings do not.** The distinction is not cosmetic — it encodes what the
system is *certain* is wrong (arithmetic) versus what it merely *suspects* is suboptimal (pedagogy).

**Client/server disagreement resolves to the server.** The client computes these rules for immediate
feedback; the server recomputes them authoritatively at submit. If they disagree — a stale buffer, a
concurrent edit, a rule the client's mirror has not caught up with — the server's `422` is applied to
the UI, the buffer is re-settled from the server's response, and the discrepancy is reported to
monitoring, because a systematic divergence is a bug in the mirror that needs fixing (§16.2, §26.6).

### 13.13 Live Preview

The preview renders the *same document model* in exam layout: org header identity, instructions, section
headings, numbered questions, marks in the margin, and page breaks. It is a **derived view** — it reads
the buffer and writes nothing.

| Concern | Approach | Why |
|---------|----------|-----|
| Source of truth | Derived from the buffer, never re-fetched | Preview must reflect unsaved edits; fetching would show stale server content and disagree with the canvas |
| Print fidelity | Print token set (§22.13) and estimated page breaks | The printed paper is the actual deliverable; a preview that misrepresents pagination is worse than none |
| Answer key | Toggle to render answers and explanations inline or as a separate section | Teachers need both artefacts from one document |
| Variant sets | Where enabled, preview per set with the shuffle applied | Reviewing set A tells you nothing about set B's layout |
| Cost containment | Debounced (~300 ms), memoized per section, deferred behind typing | Re-rendering an exam layout on every keystroke would make typing stutter (§19.9) |

Page-break estimation is explicitly an estimate, and labelled as such. Exact pagination depends on the
final render engine, so the preview shows approximate breaks and the export is authoritative. Claiming
exactness we cannot deliver would erode trust in the whole preview.

### 13.14 Undo and Redo

Every buffer mutation is expressed as an **invertible command** (`{ apply, invert, label, paths }`) and
pushed onto a bounded undo stack. Undo pops and applies the inverse; redo re-applies. Because the
document model is an addressable tree (§13.3), a command touches specific paths, which is what makes
inversion exact rather than a whole-document snapshot diff.

| Undoable | Not undoable | Why not |
|----------|--------------|---------|
| Edit a field (stem, marks, difficulty, Bloom) | A completed AI generation | The work was performed and metered; "undo" would misrepresent a refund that did not happen. Reject the candidates instead (§13.9) |
| Add / remove / rename / reorder a section | An export | The artefact exists and may already be downloaded or shared |
| Reorder a question | A submission for approval | It changed state visible to other users and notified them (§12.7) |
| Lock / unlock | A version restore | It is itself a deliberate history operation, with its own history entry |
| Accept or reject a regeneration candidate | Anything after a page reload | The stack is session-scoped by design (below) |
| Clear a slot | | |

**Scope and limits.** The stack is per-paper, per-session, capped at roughly 50 commands, and cleared on
reload and on version restore. It is not persisted: a durable undo history would have to reconcile with
server-side versions and other users' changes, which is precisely what the version model (§13.15) exists
to do. Two overlapping history mechanisms would be worse than one clear one — undo is for the last few
minutes of local editing; versions are for durable history.

**Interaction with autosave.** Undo produces a dirty buffer like any other edit, so it triggers autosave
on the normal schedule. An undo of a change that was already saved is simply a new change — the server
does not distinguish, which keeps the model simple.

Bindings are the platform-standard Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z, and every undo announces what was
reversed via a live region ("Undid: moved Question 3") — a silent undo leaves keyboard and screen-reader
users unsure whether anything happened (§21.8).

### 13.15 Autosave

```
        ┌────────┐  user edits    ┌────────┐  debounce 1.5s / blur / idle 5s  ┌────────┐
        │ clean  │──────────────▶ │ dirty  │────────────────────────────────▶ │ saving │
        └────────┘                └────────┘                                  └───┬────┘
             ▲                         ▲                                          │
             │  200 OK                 │                                          │
             │  (settle from response) │  new edit while saving                    │
             └─────────────────────────┴──────────────────────────────────────────┤
                                                                                   │
                       ┌──────────┐  409 conflict                                 │
                       │ conflict │◀───────────────────────────────────────────────┤
                       └────┬─────┘                                                │
                            │ resolved (§13.16)                     network / 5xx  │
                            ▼                                                      ▼
                       ┌────────┐                                            ┌────────┐
                       │ dirty  │                                            │ failed │
                       └────────┘                                            └───┬────┘
                                                                  retry w/ backoff│
                                                                    ┌─────────────┘
                                                                    ▼  persistent failure
                                                            queue locally (§13.18)
```

**Patch granularity.** Autosave sends the changed subtree identified by `dirtyPaths`, plus the
`revisionId` the buffer was based on — not the whole paper. A 60-question paper is a large payload;
sending it on every keystroke burst would waste bandwidth, slow saves, and make conflicts far more
likely because every save would touch every field. Patching a subtree means two users editing different
sections do not conflict at all.

**Honest indicator.** The header shows *Saved HH:MM*, *Saving…*, *Unsaved changes*, or *Save failed —
retry*. This indicator is load-bearing: it is the entire basis on which a user decides whether it is
safe to close the tab. It therefore never shows "Saved" optimistically, never lingers on "Saving…" after
a failure, and always reflects the buffer's real state (§2.3).

**Triggers and edge behaviour.** Debounced on typing (≈1.5 s), immediate on blur of a field, on an idle
interval, before navigation, and on `visibilitychange` when the tab is hidden. A `beforeunload` guard
fires when the buffer is dirty (§15.6). Because `beforeunload` cannot await a network call, the buffer is
also mirrored to `sessionStorage` on every dirty transition, which is what makes crash recovery possible
(§13.18).

**Guarantee to the user.** The UI promises exactly this: *edits are saved within a few seconds of you
stopping typing, and if a save fails you will be told and the work will be recoverable.* It does not
promise that closing the tab mid-save is safe, and the unsaved-changes guard says so.

### 13.16 Conflict Handling

Three realistic concurrency scenarios:

| Scenario | Frequency | Nature |
|----------|-----------|--------|
| The same teacher in two tabs | Common | Both buffers descend from the same revision; edits are often in different places |
| Two teachers on one paper | Occasional in team-taught programs | Genuine concurrent editing |
| A generation worker writing questions while the user edits | Common by design | The worker fills slots; the user edits structure or other slots |

**Optimistic concurrency.** Every save carries the `revisionId` the buffer was based on. The server
accepts the patch only if that is still current, otherwise it returns `409` with the current revision
and the conflicting paths.

**Resolution strategy, in order:**

1. **Auto-merge disjoint subtrees.** If the incoming change and the local change touch non-overlapping
   paths (the worker filled Section B's slots; the user renamed Section A), the client rebases its patch
   onto the new revision and retries. This is safe because the paths are provably disjoint, and it is
   the common case — which is exactly why patch-granular saves and sparse ordering (§13.7) were chosen.
2. **Scoped conflict dialog.** If the same path was changed on both sides, the client presents *only the
   conflicting nodes* — not the whole document — with **Keep mine**, **Keep theirs**, and **Compare
   side by side**, including who changed it and when.
3. **Never last-write-wins by default.** Silently overwriting is the worst option available: the losing
   user gets no signal, and their work vanishes. In an assessment tool, a silently reverted question
   could reach students.

```
 Tab A (user)                 API                     Tab B (other user)
     │  PATCH rev=41 {A.title}  │                          │
     ├─────────────────────────▶│  rev 41 → 42  ✓          │
     │◀── 200 { rev=42 } ───────┤                          │
     │                          │◀── PATCH rev=41 {A.title}┤   (based on stale rev)
     │                          ├── 409 { rev=42,          │
     │                          │        conflicts:[A.title]}▶│
     │                          │                          │  same path → dialog
     │                          │                          │  user picks Keep mine
     │                          │◀── PATCH rev=42 {A.title}┤
     │                          ├── 200 { rev=43 } ───────▶│
     │◀── paper.updated event ──┤  (A reconciles, §11.6)   │
```

The worker case is handled by the same machinery: `paper.question.ready` patches the buffer's slot
content directly (patch-grade, §11.5), and because the user's edits live on different paths, no conflict
arises. If the user *was* editing the very slot being filled, their local edit is preserved and the
incoming question is offered as a candidate rather than applied — the same accept/reject flow as
regeneration (§13.9).

### 13.17 Validation Gate and Submission

Submission is a deliberate gate, not a button that hopes for the best.

```
[Submit ▾]
   ├─ Submit for approval
   └─ Export directly (where org policy permits)
        │
        ▼
   Pre-submit checklist
   ┌──────────────────────────────────────────────────────────┐
   │ ✓ All 25 questions filled                                │
   │ ✓ Marks total 80 of 80                                   │
   │ ✓ No generation in progress                              │
   │ ⚠ Hard questions 18% vs 20% target        (warning)      │
   │ ⚠ Q4 and Q11 are very similar             (warning)      │
   │ ✗ Section C has no title                  (blocks)       │
   └──────────────────────────────────────────────────────────┘
     Errors are click-to-navigate; warnings are acknowledgeable
```

Errors are listed with a link that scrolls to and focuses the offending node — a validation message the
user cannot locate is only marginally better than no message. Warnings require an explicit
acknowledgement, which is recorded with the submission so an approver can see the author was aware.

The server re-validates on submit. A `422` maps back onto the checklist; a `400` business-rule failure
(approval policy, quota) renders as an inline explanation with the recovery action (§25.6).

### 13.18 Failure Handling

| Failure | Detection | Client behaviour | User sees |
|---------|-----------|------------------|-----------|
| Generation timeout | No terminal event within the job's expected window | Query job status directly; keep partial results | "Generation is taking longer than expected" with Check status / Cancel |
| LLM upstream failure (502) | `paper.generation.failed` with an upstream `ehRef` | Preserve all filled slots; mark failed slots retryable | "The AI service is temporarily unavailable — retry these 3 questions" |
| Partial generation | Terminal event carries per-slot outcomes | Materialize successes; mark failures individually | Paper usable; failed slots clearly flagged with per-slot retry |
| Quota exhausted | `429`, or `400` business rule at submission | Block the action before dispatch; refetch quota | Quota state with an upgrade/wait path (§12.9) |
| Autosave failure (5xx/network) | Save state → `failed` | Retry with backoff; keep the buffer dirty; mirror to `sessionStorage` | "Save failed — retrying" then "Retry now"; nothing lost |
| SSE loss during generation | Heartbeat window elapses (§11.11) | Reconnect with `Last-Event-ID`; reconcile by refetching job status and the paper | Brief "reconnecting" indicator; progress resumes |
| Browser crash / accidental close | `sessionStorage` mirror present on next load with a newer timestamp than the server revision | Offer recovery: compare local draft with the server version | "We recovered unsaved changes — review and keep or discard" |
| Session expired mid-edit | `401` on autosave | Single-flight refresh and replay (§10.9); if refresh fails, preserve the buffer, prompt re-login, restore after | Re-login prompt; edits survive |
| Conflict (409) | Save rejected | Auto-merge or scoped dialog (§13.16) | Nothing, or a targeted conflict dialog |

The through-line: **no failure path discards the user's work.** Every branch either preserves the buffer,
mirrors it locally, or offers explicit recovery.

### 13.19 Performance

The builder is the application's heaviest surface and its budgets are the strictest (§19.2).

| Technique | Where | Why |
|-----------|-------|-----|
| Virtualization | Canvas card list beyond ~40 cards | A 100-question paper would otherwise mount hundreds of interactive subtrees; virtualization must preserve keyboard order and ARIA relationships (§21.6) |
| Per-card memoization | Every question card | A card re-renders only when its own slot data changes — the difference between an edit costing one card and costing the whole canvas |
| Uncontrolled inputs | All card and blueprint fields (React Hook Form) | Typing does not re-render the form tree; this is the primary reason RHF was chosen (§3.7) |
| Narrow store selectors | Buffer subscriptions | A card subscribes to its slot, not the document; subscribing broadly is the classic way to make this surface stutter (§9.11) |
| Deferred preview | Right pane | Preview render is debounced and deferred off the typing path (§13.13) |
| Memoized derivations | Marks and distribution summaries | Recomputed per section, not per paper, on each change (§13.10) |
| Dynamic imports | Rich editor, drag-and-drop, preview renderer, comparison view | Keeps the initial builder bundle small; loaded on first use (§19.5) |
| Coalesced events | Generation progress | Bounded by frame rate, not event rate (§11.13) |

The measured budgets that matter here: interaction latency on a keystroke and on a drag within one
frame; time from generation submit to first question rendered; and autosave latency, all attributed to
this route in field telemetry (§26.3).

### 13.20 Accessibility

| Concern | Requirement |
|---------|-------------|
| Reordering | Full keyboard reorder with position announcements (§13.7, §21.9) — the builder's most important a11y obligation |
| Structure | Sections as landmarks/regions with headings; cards as list items in a correctly-ordered list |
| Async state | Generation progress, autosave state, and completion announced politely and throttled so a progress stream does not flood a screen reader (§21.8) |
| Validation | Errors associated with their fields; the submit checklist focuses the first error |
| Undo | Announced with what was reversed |
| Three panes | Each pane is a labelled region reachable by landmark navigation; the preview is `aria-hidden` from the tab order as a derived view, with an accessible summary and the answer-key table alternative |
| Virtualization | Off-screen cards must not break tab order or `aria-setsize`/`aria-posinset` semantics |
| Distribution bars | Never colour-only: numeric values and target deltas are in text (§21.10) |

### 13.21 Scalability and Extensibility

| Future need | How the model accommodates it |
|-------------|------------------------------|
| New question types (matching, assertion-reason, case-based) | A slot's shape is driven by its question type via a discriminated schema (§15.10); a new type adds a schema variant and a card renderer, not a change to the document model |
| New distribution dimensions (topic coverage, cognitive load) | Targets and actuals are a generic map of dimension → distribution; the visualization is one shared component |
| Multi-set / variant papers | Sets are a projection over the same document (shuffle seed + per-set overrides), which is why the preview already treats set as a parameter (§13.13) |
| Longer papers | Virtualization and patch-granular saves already scale; the constraint becomes the preview, which is why it is deferred and memoized |
| Collaborative editing | The document is an ordered tree of addressable nodes with invertible commands and optimistic concurrency — the right foundation for OT or CRDT work, though the transport would need revisiting (§28.4) |
| AI Copilot in the builder | Proposals map onto the existing candidate accept/reject flow, so a copilot suggestion is delivered through machinery that already exists (§28.6) |

### 13.22 Paper Builder Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Client state | A rich editor buffer, the one sanctioned exception (§2.15) | Sub-frame editing on a large document is impossible if every keystroke is server state | A buffer/server boundary that must be policed; autosave and conflict machinery required |
| Document shape | Ordered tree of addressable nodes | Enables patch saves, invertible undo, subtree merges, and future collaboration | More complex than a flat question array |
| Slot ordering | Sparse/fractional indices | One field write per move; disjoint reorders merge cleanly | Occasional renormalization |
| Slots before questions | Structure renders immediately; questions materialize | No blank screen during a multi-minute generation | Slot state machine to manage |
| Generation transport | `202` + SSE, never a held request | Real progress, survives blips, no held connections (§10.10) | Two mechanisms to reason about |
| Regenerated questions | Candidates requiring accept, never auto-applied | Never silently replace content a teacher reviewed | An extra click per regeneration |
| Reorder | Optimistic | Safe, reversible, high-frequency (§9.8) | Rollback path to implement |
| Generation / submit / export | Never optimistic | Consequential and irreversible; a false success would be a serious misrepresentation | Users wait for confirmation |
| Locking | A flag orthogonal to card state | Avoids a combinatorial state explosion; a lock protects content, not position | Lock semantics must be explained in the UI |
| Marks rules | Errors block, distributions warn | Encodes certainty: arithmetic is objective, pedagogy is a judgement | Users can ship an off-target distribution (correctly) |
| Bloom targets | Advisory only | The classification is interpretive and often AI-assigned; hard enforcement would be false precision | Two tolerance behaviours to explain |
| Client vs server validation | Client mirrors, server decides | Fast feedback without duplicating authority (§16.2) | Mirror drift must be monitored |
| Preview | Derived from the buffer, debounced, page breaks estimated | Must reflect unsaved edits; must not cost typing performance | Estimated pagination is not exact, and says so |
| Undo | Session-scoped, bounded, invertible commands | Local recent editing; durable history belongs to versions | No undo across reloads |
| Autosave | Patch the dirty subtree, honest indicator, local mirror | Small payloads, fewer conflicts, crash recovery | More moving parts than whole-document save |
| Conflicts | Auto-merge disjoint, dialog on overlap, never LWW | Silent overwrites are unacceptable in an assessment tool | Merge logic and a conflict UI to build and test |
| Layout | Focused variant, three panes | Specify/edit/verify simultaneously visible builds trust in AI output | A second layout; not viable on phones (§20.4) |

---

## 14. Component Architecture

### 14.1 Four Tiers

```
┌──────────────────────────────────────────────────────────────────────────┐
│ TIER 4 · Feature components        features/<name>/components            │
│   BlueprintForm · QuestionCard · ApprovalQueueRow · IngestionStageList    │
│   knows the domain · may use feature hooks and the query cache            │
├──────────────────────────────────────────────────────────────────────────┤
│ TIER 3 · Layout components         components/layout                     │
│   Sidebar · Header · Breadcrumb · OrgSwitcher · AppShell                  │
│   knows the shell, not the domain                                        │
├──────────────────────────────────────────────────────────────────────────┤
│ TIER 2 · Shared composed           components/shared                     │
│   DataTable · EmptyState · ErrorState · ConfirmDialog · FilterBar …       │
│   domain-agnostic, app-specific patterns                                 │
├──────────────────────────────────────────────────────────────────────────┤
│ TIER 1 · Primitives                components/ui   (owned shadcn source)  │
│   Button · Dialog · Popover · Select · Tabs · Toast · Tooltip …           │
│   accessible behaviour, no app knowledge whatsoever                      │
└──────────────────────────────────────────────────────────────────────────┘
                        dependencies point downward only
```

| Tier | Examples | May depend on | May **not** depend on | Owner |
|------|----------|---------------|----------------------|-------|
| 1 · Primitives | `Button`, `Dialog`, `Select`, `Toast` | Nothing but `lib/`, `utils/`, styling tokens | Anything app-specific | Design system group |
| 2 · Shared | `DataTable`, `ConfirmDialog`, `EmptyState`, `Pagination` | Tier 1, `hooks/`, `types/` | `features/*`, `services/*`, `stores/*` (except `ui`) | Shared, reviewed broadly |
| 3 · Layout | `Sidebar`, `Header`, `Breadcrumb`, `OrgSwitcher` | Tiers 1–2, `hooks/`, `stores/` (`session`, `ui`) | `features/*` internals | Platform group |
| 4 · Feature | `QuestionCard`, `BlueprintForm`, `ApprovalQueueRow` | Tiers 1–3, its own feature's hooks/api, shared query hooks | Another feature's internals (§4.5) | The owning feature team |

**Which tier does a new component belong to?** Three questions, in order: *Does it know anything about
papers, questions, or organizations?* → Tier 4. *Is it shell chrome?* → Tier 3. *Is it a generic pattern
that two unrelated features would both want?* → Tier 2. *Is it a behaviour primitive with no app
knowledge?* → Tier 1. Components are promoted upward (Tier 4 → Tier 2) only on the **second** genuine
consumer, never in anticipation — premature generalization produces components with a dozen props that
serve nobody well.

**Why primitives are copied source, not a dependency.** shadcn/ui components live in our repository
(§3.6). We can restyle, extend behaviour, and fix a bug immediately, and we inherit Radix's correct
accessibility. The cost is that upgrades are manual, which we accept in exchange for not fighting a
black-box library's opinions on a product with distinctive layout needs.

### 14.2 Server and Client Component Boundary

The App Router splits every component into server-rendered or client-hydrated. This is an architectural
decision per component, not an incidental one.

**The rule: default to server; promote to client only for interactivity, state, effects, or browser
APIs — and push the boundary as deep as possible.**

```
app/(auth)/(teacher)/question-bank/page.tsx        ── server
│  fetch first page + facets, prefetch into the query cache
├── <PageHeader/>                                   ── server (static content)
├── <FilterBar>                                     ── server shell…
│     └── <FilterControls/>                         ──   client leaf (interactive)
├── <QuestionList>                                  ── server list container
│     └── <QuestionRow>                             ── server row content
│           └── <RowActions/>                       ──   client leaf (menu, dialogs)
└── <Pagination/>                                   ── client (URL state)

   ▲ server renders the bulk of the markup; only the small interactive leaves hydrate
```

**Why push the boundary deep.** Everything under a client component becomes client code, whether it
needs to be or not. A page marked client at the top ships its entire subtree to the browser and hydrates
all of it — the single most common way a Next.js application ends up with a large bundle and slow
interaction readiness. Keeping the boundary at the leaves means the question list's markup costs nothing
to hydrate while its action menus remain fully interactive.

| Belongs on the server | Belongs on the client |
|-----------------------|----------------------|
| Data fetching for initial render, prefetching into the query cache | Anything using state, effects, or refs |
| Static and derived markup | Event handlers beyond native form submission |
| Access to server-only configuration | Browser APIs (storage, media queries, `AbortController` in UI code) |
| Large formatting or content transformation | The SSE hub, stores, and query hooks |
| Role-based *composition* (which widgets to render) | Role-based *interactivity* (`useCan()` on an action) |

**Presentational components never fetch.** A Tier 1–3 component that fetches is unreusable (it drags a
data dependency wherever it goes), untestable without a network mock, and a guaranteed request waterfall
when rendered in a list. Data enters through props from a server component or through a query hook in a
Tier 4 component. This is enforced by the import boundaries in §4.5: `components/*` cannot import
`services/*`.

### 14.3 Communication Patterns

| Pattern | Use when | Example | Why not something else |
|---------|----------|---------|----------------------|
| Props down | Parent has the data the child renders | `QuestionCard` receives its slot | Simplest, most explicit, most testable |
| Callbacks up | Child reports an event the parent owns | Card's `onLock` handled by the section | Keeps state ownership with whoever can act on it |
| Composition / slots | A component needs arbitrary content in defined positions | `DataTable` toolbar, empty, and row-action slots | Avoids the boolean-prop explosion that configuration props become |
| React Context | A stable value needed throughout a subtree | Theme, form context, table instance | Cheap only when the value changes rarely — context re-renders every consumer |
| Zustand store | Genuinely global client state | Session, UI prefs, editor buffer, upload queue | Narrow selectors avoid context's broad re-render problem (§9.11) |
| Query cache | Distant components need the same server truth | Breadcrumb reading the paper title the page already fetched (§8.5) | No prop drilling, no duplication, automatic invalidation |

The query-cache row is the most under-used pattern on most teams and the most valuable here. When the
breadcrumb needs a paper's title, threading it through four layout levels would couple the shell to
every page. Reading `[orgId,'papers',paperId]` from the cache costs nothing (the page already fetched
it) and stays correct when the paper is renamed.

### 14.4 Props Design

| Convention | Rationale |
|-----------|-----------|
| Small, explicit prop surfaces | A component with 15 props is usually three components |
| Discriminated unions over boolean soup | `variant: 'error' \| 'warning' \| 'info'` beats `isError`/`isWarning`, which permits nonsense combinations the type system should reject |
| Composition over configuration | Pass a node, not fifteen flags describing what to render |
| Controlled / uncontrolled pairs on primitives | `value`/`defaultValue` with `onChange`, matching native semantics so consumers are not surprised |
| Forward refs and spread native attributes | Primitives must be usable as drop-in replacements for the element they wrap, including `aria-*` and `data-*` |
| Polymorphic `asChild` | Render a Button's behaviour on a Link without nesting interactive elements (an accessibility bug) |
| Merged `className` last | Every component accepts and merges a class override, so consumers can adjust spacing without a fork |
| Never pass whole entities where a field suffices | Narrows re-render surface and clarifies the dependency |

### 14.5 State Ownership

State lives at the **lowest common ancestor that needs it**, and is lifted only when a second consumer
genuinely appears. Colocation keeps components independently testable and prevents the slow drift where
a page component accumulates every child's state.

| State | Home | Reason |
|-------|------|--------|
| Dropdown open, hover, inline-edit toggle | The component | No one else needs it |
| Selected rows in a table | The table's container | The bulk-action bar is a sibling |
| Filters and search | The **URL** (§18.5) | Shareable, reloadable, back/forward-correct |
| Form values | React Hook Form (§15) | Purpose-built; avoids re-render cost |
| Paper draft | `paper-builder` store (§13.3) | Spans three panes and outlives individual components |
| Session, theme, uploads, toasts | Their stores (§9.4) | Genuinely global |

### 14.6 Shared Component Inventory

| Component | Purpose | Notable behaviour |
|-----------|---------|-------------------|
| `DataTable` | Headless table model + styled shell | Column priority for responsive collapse (§20.6), sorting mapped to the `sort` param, row selection, sticky header, virtualization opt-in |
| `EmptyState` | The three empty kinds (§25.6) | Distinguishes "no data yet" / "no results for these filters" / "nothing left to do", each with a different action |
| `ErrorState` | Scoped failure with recovery | Retry action, plain-language cause, and the `requestId` behind a details affordance |
| `ConfirmDialog` | Destructive-action confirmation | Names the specific consequence ("Delete Section C and its 8 questions"), types-to-confirm for high-impact actions, focus and escape handled by the primitive |
| `PageHeader` | Title, description, breadcrumb slot, primary actions | Single place where page-level actions are `<Can>`-gated |
| `FilterBar` | Facet filters + search + saved views | Writes to the URL, debounces free text, immediate on facet toggles (§18.6) |
| `StatusBadge` | Paper/job/ingestion status | Colour **plus** icon **plus** text — never colour alone (§21.10) |
| `ProgressIndicator` | Determinate and indeterminate progress | Announces politely and throttled; distinguishes phases (§12.4) |
| `DistributionBar` | Actual vs target distribution | Numeric values in text alongside the bar; used for difficulty and Bloom (§13.10) |
| `MetricCard` | Dashboard/analytics single metric | Own loading, error, and empty states so it fails in isolation (§12.2) |
| `FileDropzone` | File selection and drop | Client-side advisory validation, queue handoff to the `uploads` store (§17.9) |
| `Pagination` | Numbered pagination | Offset surfaces only; infinite lists use their own loader (§18.9) |
| `CommandPalette` | Keyboard-first navigation and actions | Entries filtered by `useCan()`; lazily loaded on first invocation |

### 14.7 Compound and Headless Patterns

**Compound components** are used where a component has several coordinated parts whose arrangement the
consumer should control: `DataTable` (root, toolbar, header, body, empty, pagination), the question
card (header, body, footer, actions), and dialogs. The parts share state through a scoped context, so
the consumer composes freely without a prop for every permutation.

**Headless logic** is used where behaviour and presentation should vary independently: table state,
combobox filtering, and virtualization all come from headless libraries (§3.14) with our own markup.
This is what lets the question bank's list and the builder's card list share selection and virtualization
behaviour while looking nothing alike.

### 14.8 Naming and File Conventions

One component per file, named for what it *is* rather than where it appears (`ApprovalQueueRow`, not
`AdminTableRow2`). Files match the exported component name. A component with sub-parts gets a directory
with an `index.ts`. Client components are marked at the narrowest possible file. Test files sit beside
their component. Nothing is exported from a feature except through its `index.ts` (§4.4).

### 14.9 Anti-Patterns

| Anti-pattern | Why it hurts | Instead |
|--------------|--------------|---------|
| Prop drilling past two levels | Every intermediate component is coupled to data it does not use | Query cache, store, or composition |
| Context for high-frequency values | Every consumer re-renders on every change — the reason context is wrong for the editor buffer (§9.14) | Zustand with narrow selectors |
| Fetching in a leaf component | Waterfalls, untestable, unreusable | Fetch at the route/feature level; pass down or use the cache |
| God component | Unreviewable, untestable, permanently merge-conflicted | Split by responsibility; extract compound parts |
| Styling by cascade / descendant selectors | Breaks when a component moves | Utilities on the component; variants via props |
| Duplicating a primitive | Divergent focus and keyboard behaviour; accessibility regressions | Extend the primitive or add a variant |
| Client component at the page root | Ships and hydrates the whole subtree | Push the boundary to interactive leaves (§14.2) |
| Boolean prop explosion | Permits invalid combinations | Discriminated union variants |
| Role checks scattered inline | Impossible to audit; drifts from the backend | `useCan()` / `<Can>` exclusively (§7.3) |

### 14.10 Testability

The tier system produces a natural testing strategy: primitives are tested for keyboard and ARIA
behaviour; shared components are tested across their full state matrix (loading, empty, error,
disabled-with-reason); feature components are tested against a mocked HTTP boundary returning the real
envelope shape. Because presentational components never fetch, most of the tree is testable with props
alone — the property that makes the component suite fast enough to run on every commit (§27.4).

### 14.11 Component Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Hierarchy | Four tiers with downward-only dependencies | Predictable placement; lint-enforceable; prevents cycles | Occasional judgement calls at tier boundaries |
| Primitives | Owned shadcn/Radix source | Full control plus accessible defaults | Manual upstream updates |
| Promotion to shared | Only on the second real consumer | Avoids premature generalization | Brief duplication before extraction |
| RSC boundary | Server by default, client at the leaves | Minimizes hydration and bundle cost | Requires deliberate thought per component |
| Fetching | Never in presentational components | Reuse, testability, no waterfalls | Data must be threaded or read from the cache |
| Cross-tree data | Query cache over prop drilling | No coupling; stays correct on invalidation | Requires key discipline (§9.5) |
| Configuration | Composition and slots | Avoids prop explosions and invalid states | More verbose call sites |
| Cross-cutting values | Context only when stable; store otherwise | Context re-renders all consumers | Two mechanisms to choose between |
| Permission checks | `useCan()` / `<Can>` only | Auditable and consistent with the backend mirror | Contributors must not hand-roll role checks |

---

## 15. Form Architecture

### 15.1 The Stack and Its Separation of Concerns

| Concern | Owner | Why separate |
|---------|-------|--------------|
| Form state (values, dirty, touched, submission) | React Hook Form | Uncontrolled inputs mean typing does not re-render the tree — essential for the blueprint and question editors (§3.7) |
| Validation rules and types | Zod | One schema yields the runtime check *and* the inferred TypeScript type, so "what we validate" cannot drift from "what we typed" |
| Presentation and accessibility wiring | shadcn form primitives | Label association, description and error `aria-describedby`, invalid state — done once, correctly |

The **resolver** is the bridge: React Hook Form validates through the Zod schema and receives back a map
of field paths to messages, which the presentation layer renders in place. The consequence worth
stating: adding a rule means editing one schema, and the type, the runtime check, and the error display
all follow. No rule lives in three places.

### 15.2 Layer Model

```
┌─ Wizard ──────────────────────────────────────────────────┐
│  step orchestration, per-step gating, resumability         │
│  ┌─ Form ────────────────────────────────────────────────┐ │
│  │  one Zod schema · submission · server error mapping   │ │
│  │  ┌─ Fieldset / Section ──────────────────────────────┐│ │
│  │  │  grouping, legend, cross-field rules within group ││ │
│  │  │  ┌─ Field ─────────────────────────────────────┐  ││ │
│  │  │  │ label · control · hint · error · a11y wiring│  ││ │
│  │  │  └─────────────────────────────────────────────┘  ││ │
│  │  └───────────────────────────────────────────────────┘│ │
│  └───────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

| Layer | Owns | Does not own |
|-------|------|--------------|
| Field | One value, its label, hint, error display, and ARIA wiring | Knowledge of other fields |
| Fieldset | Grouping, legend, rules spanning its own fields | Submission |
| Form | The schema, defaults, submission, server error mapping, dirty state | Navigation |
| Wizard | Step order, per-step gating, resumability | A separate data model — it shares the form's single schema (§15.9) |

### 15.3 Lifecycle

```
server data ──▶ defaultValues ──▶ register fields ──▶ user edits
                                                          │
                                      validate per timing policy (§15.4)
                                                          │
                                                     submit
                                                          │
                                   ┌──────────────────────┴───────────────────────┐
                                   ▼                                              ▼
                              200 / 201                                    422 validation
                     settle: reset with the server's                map error.details → fields
                     authoritative response; dirty=false            focus first error; stay dirty
                                   │                                              │
                                   │                          400 business rule ──┴─▶ form-level alert
                                   │                          409 conflict ─────────▶ conflict flow (§13.16)
                                   ▼
                          invalidate related queries (§9.6)
```

**Reset with the server's response, not with local values.** The server may normalize (trim, round
marks, canonicalize a slug) or compute derived fields. Resetting from the response makes the form show
what was actually stored and clears the dirty flag honestly. Resetting from local values leaves the user
looking at input that differs from the record.

### 15.4 Validation Timing

| Mode | Applied to | Reason |
|------|-----------|--------|
| `onBlur` | Most fields | Validating while typing marks a field invalid before the user has finished — noisy and discouraging |
| `onChange` | Constrained numerics with live feedback (marks, counts, distribution percentages) | The user needs to see the total update as they type; the feedback *is* the point (§13.12) |
| `onSubmit` | Expensive or cross-field rules (paper totals, structural completeness) | Too costly or too noisy to run continuously |
| Debounced async | Server-checked uniqueness (template name, invite email) | Requires a round-trip; debounced ~500 ms, superseded requests cancelled (§16.10) |
| Re-validate `onChange` after first submit | All fields | Once the user has seen an error, immediate confirmation that they fixed it is helpful rather than noisy |

### 15.5 Dirty and Touched State

| Consumer | Uses | Behaviour |
|----------|------|-----------|
| Save indicator | `isDirty` | Drives the builder's honest save state (§13.15) |
| Navigation guard | `isDirty` | Blocks route change and unload with a confirm (§15.6) |
| Autosave trigger | `isDirty` + debounce | Only dirty forms save |
| Submit enablement | `isDirty` + `isValid` | An always-enabled submit that fails on click is worse than a disabled one with a reason |
| Error display | `isTouched` | Never show an error on a field the user has not visited |

Dirty is computed against the *server's* values, not against empty — which is why resetting from the
server response (§15.3) is what makes the dirty flag trustworthy.

### 15.6 Unsaved-Changes Guard

The guard is implemented **once at the layout level**, not per form. A form registers a dirty-state
provider; the layout intercepts route changes and `beforeunload` whenever any registered form is dirty.

**Why centrally.** Per-form guards are forgotten on new forms, duplicate the interception logic, and
conflict when two dirty forms coexist. One layout-level guard is auditable and applies uniformly. For
the builder, the guard is softened by autosave (§13.15): it fires only when a save is genuinely pending
or failed, because prompting a user who has autosave working correctly is noise that teaches them to
dismiss the dialog reflexively.

### 15.7 Error Message Architecture

| Source | Presentation | Announcement |
|--------|-------------|--------------|
| Field schema (Zod) | Inline below the field, `aria-describedby` | Announced on blur/submit via the field's error region |
| Cross-field rule | On the most relevant field, or on the fieldset when it belongs to no single field | Polite live region |
| Form-level summary (long forms) | Summary block at the top listing every error, each a link that focuses its field | Focus moves to the summary on failed submit |
| Server `422` `error.details` | Mapped onto the named fields | Same as schema errors — indistinguishable to the user, correctly |
| Server `400` business rule | Form-level alert with the recovery action | Assertive if it blocks; polite otherwise |
| Unattributable server error | Form-level alert with `requestId` behind a details affordance | Polite |

The `422` mapping matters: `error.details` names the field paths that failed, so a server-side rule the
client did not mirror still lands on the right input rather than as a generic banner. When a path cannot
be matched to a field (a rule about a relationship the form does not expose), it degrades to the
form-level alert rather than being silently dropped (§16.13).

### 15.8 Autosave vs Explicit Submit

| | Explicit submit | Autosave |
|---|---|---|
| Used by | Auth, invite, settings, template create, question editor | Paper Builder (§13.15) |
| Trigger | User action | Debounce, blur, idle, navigation |
| Validation | Full schema before dispatch | Field-level only; full validation deferred to the submit gate (§13.17) |
| Failure | Inline errors, user retries | Retry with backoff, save-state indicator, local mirror |
| Confirmation | Toast or navigation | Save-state indicator only — a toast per autosave would be intolerable noise |

Autosave deliberately does **not** block on full-document validity. A half-finished paper must still be
saved; enforcing completeness at every keystroke would make the builder unusable. Completeness is
enforced at the submission gate instead.

### 15.9 Dynamic, Nested, and Dependent Forms

**Array fields** (sections, question slots, MCQ options) support add, remove, and reorder with **stable
field keys** — identity comes from the item's own id, never from its array index. Using the index makes
React reuse the wrong DOM node after a removal or reorder, producing the classic bug where deleting the
second option clears the third's text. Array-level constraints (at least one section, option count by
question type, attempt-any-N < available) are expressed on the array in the schema, not on its items.

**Dependent schemas** use discriminated unions: question type is the discriminant, and each variant
declares its own fields (MCQ requires options and a correct answer; short answer requires an expected
answer and a marking guide). Changing the type switches the schema branch, and fields that do not exist
in the new branch are dropped rather than lingering as invisible invalid values. A single flat schema
with everything optional would validate nothing useful.

**Nested forms in the DOM are avoided entirely** — nested `<form>` elements are invalid HTML with
unpredictable submission behaviour. Composite editing surfaces use one form with nested fieldsets, or
separate forms rendered as siblings coordinated by their parent.

### 15.10 Multi-Step Wizards

Used by the blueprint flow (curriculum → structure → targets → review) and organization onboarding.

| Concern | Approach | Why |
|---------|----------|-----|
| Data model | **One schema** for the whole wizard, with per-step field subsets | Cross-step rules (total marks vs section marks) are expressible; one submit payload; no reconciliation between step schemas |
| Step state | Current step in the URL | Reload-safe, shareable, back/forward behaves (§18.5) |
| Per-step gating | Validate only that step's fields to advance | Blocking on later steps' fields would be nonsense |
| Final validation | Full schema on submit | Cross-step rules can only be checked once everything exists |
| Resumability | Values persist in form state across steps; the builder's blueprint additionally autosaves | Going back must not lose input |
| Review step | Read-only summary with links back to each step | Users must be able to verify before committing to a metered AI operation |

### 15.11 Large-Form Performance

The blueprint form and the builder's card grid are the stress cases. Four mechanisms keep them fast, all
consequences of the RHF choice in §3.7:

| Mechanism | Effect |
|-----------|--------|
| Uncontrolled inputs | A keystroke updates the DOM node, not React state — no re-render of the form tree |
| Isolated field subscriptions | A component watching one field re-renders only when that field changes |
| Deferred whole-form derivations | Marks and distribution totals are memoized and recomputed per section, not per keystroke (§13.19) |
| Virtualized field arrays | Long slot lists render only visible rows, preserving semantics and tab order (§19.10) |

Watching the entire form value — the tempting one-liner — re-renders on every keystroke and is the
single most common way this surface becomes sluggish. It is called out explicitly in review.

### 15.12 Form Accessibility

Every field has a programmatically associated label (never placeholder-as-label), hints and errors
associated via `aria-describedby`, `aria-invalid` on failure, and `aria-required` where required. Related
fields are grouped in a fieldset with a legend. On failed submit, focus moves to the error summary (long
forms) or the first invalid field (short forms), and the error is announced politely. Errors are never
conveyed by colour alone. Full requirements in §21.11.

### 15.13 File Inputs

File selection integrates with the upload pipeline rather than posting bytes with the form. The
`FileDropzone` component performs advisory client validation (type, size, page count), hands the file to
the `uploads` store, and the form field holds only the resulting upload reference. This keeps the form's
submit fast and lets the upload continue while the user keeps working (§17.9).

### 15.14 Form Archetypes

| Archetype | Notable characteristics |
|-----------|------------------------|
| Auth forms | Short, `onBlur`, server errors inline, rate-limit-aware submit (§12.1) |
| Blueprint form | Multi-step wizard, one schema, live numeric feedback, debounced server-side feasibility check (§13.4) |
| Question editor | Discriminated-union schema by question type, array fields for options, inline vs drawer split (§13.6) |
| Template form | Async uniqueness check on name, structural completeness (§16.9) |
| User invite | Async email uniqueness, role select gated by `useCan()`, bulk variant with per-row errors |
| Org settings | Sectioned form, per-section save, branding fields with contrast validation (§22.12) |
| Filter forms | Not a form in the submit sense — writes to the URL, debounced, no dirty state (§18.5) |

### 15.15 Form Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Form state | React Hook Form, uncontrolled | Large forms stay responsive; the core reason for the choice (§3.7) | Less familiar than controlled inputs |
| Validation | Zod schemas as the single source | One rule → runtime check + type + error map | Schemas must mirror the server deliberately (§16.2) |
| Timing | Blur by default, change for live numerics | Avoids premature errors while keeping feedback where it matters | A per-field policy to apply consistently |
| Reset | From the server's response | Dirty state and displayed values stay honest | Requires the API to return the full record |
| Unsaved guard | Once, at the layout level | Uniform and auditable; no per-form omissions | Forms must register their dirty state |
| Server errors | `422` details mapped onto fields | Server-only rules land in the right place | Requires a stable field-path contract |
| Dependent fields | Discriminated unions | Invalid combinations are unrepresentable | More schema variants |
| Wizards | One schema, step in the URL | Cross-step rules work; reload-safe | Step subsets must be declared |
| Array identity | Item ids, never indices | Prevents the wrong-node-reuse class of bug | Items need stable ids |
| Autosave | Field validation only; completeness at the gate | A half-finished paper must still save | Two validation moments to explain |

---

## 16. Client-Side Validation

### 16.1 Philosophy

**Client validation mirrors server validation; it never replaces it.** The client validates to give
feedback in milliseconds instead of a round-trip. The server validates because it is the only party that
can be trusted, has the full data, and owns the business rules. When they disagree, the server wins,
always.

Three consequences follow, and all three are load-bearing:

1. The client may be **more permissive** than the server (a rule it cannot evaluate, such as
   feasibility) but must never be **more restrictive** — a client-only restriction blocks work the
   platform would have accepted, and the user has no recourse because no server response explains it.
2. A `422` or business `400` is a **normal, designed-for path**, not an exceptional failure. Every form
   maps it back onto fields (§15.7).
3. Mirrored rules **drift**. Drift must be managed deliberately, not hoped away (§16.2).

### 16.2 Keeping the Mirror in Sync

| Approach | Assessment |
|----------|-----------|
| Hand-written client schemas, hand-synced | Rejected as the long-term answer — drift is guaranteed and silent |
| **Shared constants + generated types** from the backend's OpenAPI schema | **Preferred.** The backend is Python (Pydantic) and this client is TypeScript (Zod), so a single runtime schema object cannot be literally shared. Instead the limits and enums in `packages/contracts` are published as language-neutral constants, and the client's TypeScript types are generated from the FastAPI OpenAPI document in CI — so a backend change that the client has not absorbed fails the build rather than drifting silently. |
| Schemas generated from the backend's OpenAPI/DTO definitions | Acceptable alternative where a shared package is impractical; generation runs in CI so a stale mirror fails the build |

The trade-off of a shared package is coupling: the two deployables share a versioned dependency and must
coordinate its upgrades. We accept it, because the alternative failure — a client that silently accepts
input the server rejects, or blocks input the server would accept — degrades the product continuously
and invisibly.

Whatever the mechanism, **disagreement is monitored**. Any `422` for a rule the client believed it had
mirrored is reported to monitoring as a mirror-drift signal (§13.12, §26.6). A rising rate of such
events is a defect in the mirror, not user error.

### 16.3 Validation Layers

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1 · Input constraint      type=number, min, max, maxlength, inputmode        │
│      knows: the single input        cannot know: anything else               │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2 · Field schema (Zod)    format, range, enum, required                      │
│      knows: one field's value       cannot know: siblings                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3 · Cross-field schema    totals, sums, dependent requirements               │
│      knows: this form's values      cannot know: other records               │
├─────────────────────────────────────────────────────────────────────────────┤
│ 4 · Client business rule  mirrored domain rules, projected eligibility       │
│      knows: cached org data         cannot know: concurrent changes, policy  │
├═════════════════════════════════════════════════════════════════════════════┤
│ 5 · Server validation     the same rules, authoritatively            422     │
│      knows: the real record         ← the trust boundary                     │
├─────────────────────────────────────────────────────────────────────────────┤
│ 6 · Server business rule  quota, feasibility, BR-6, concurrency      400/409 │
│      knows: everything                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

Layers 1–4 exist for speed. Layers 5–6 exist for truth. The double line is the trust boundary: nothing
above it is a security or correctness control (§7.1, §24.9).

### 16.4 Marks Validation

The complete marks rule catalogue — per-question bounds, section subtotals, choice-group arithmetic,
paper total versus blueprint total, granularity, and their severities and evaluation sites — is
specified in **§13.12** and is not restated here. Two additions beyond that table:

| Rule | Scope | Severity | Evaluated | Message intent |
|------|-------|----------|-----------|----------------|
| Marks per question within the org's configured bounds | Slot | Error | Client + server | "Your organization allows 1–10 marks per question" |
| Total marks within the program's allowed range | Blueprint | Error | Client + server | "Papers for this program are between 20 and 100 marks" |

Both depend on org and program configuration that the client already holds as cached reference data, so
they mirror cleanly. The choice-group rule (attempted × marks, not available × marks) is the single most
error-prone calculation in the product and lives in one shared, exhaustively-tested utility (§27.4).

### 16.5 Blueprint Validation

| Rule | Scope | Severity | Evaluated | Message intent |
|------|-------|----------|-----------|----------------|
| Program, class, and subject selected | Blueprint | Error | Client + server | "Choose a program and class first" |
| At least one chapter selected | Blueprint | Error | Client + server | "Select the chapters this paper covers" |
| Every chapter/topic belongs to the selected program | Blueprint | Error | Client + server | "Some chapters aren't part of this program" |
| At least one section | Blueprint | Error | Client + server | "A paper needs at least one section" |
| Every section's question count > 0 | Section | Error | Client + server | "Section B has no questions" |
| Duration within program bounds | Blueprint | Error | Client + server | "Duration must be between 30 and 240 minutes" |
| Duration plausible for total marks | Blueprint | Warning | Client | "3 hours for 80 marks is unusual for this program" |
| **Feasibility** — enough bank + source material to satisfy the blueprint | Blueprint | Error at generate | **Server only** | "Section C needs 8 hard questions from Chapter 4; only 3 are available" |

**Why feasibility is server-only.** Answering it requires counting matching questions across the bank by
chapter, type, and difficulty, and judging whether AI gap-fill can cover the remainder from indexed
source material. The client holds none of that and could only guess. Rather than approximate — and
sometimes wrongly block a user — the client asks the server (debounced, §13.4) and reports the answer.
This is the canonical example of §16.1's first rule: better permissive-and-corrected than
restrictive-and-wrong.

### 16.6 Question Count Validation

| Rule | Scope | Severity | Evaluated | Message intent |
|------|-------|----------|-----------|----------------|
| Section count matches its filled slots | Section | Error at submit | Client + server | "Section A declares 10 questions but has 8" |
| Choice group: attempt count < available count | Section | Error | Client + server | "Students must have more questions than they attempt" |
| Choice group: available count ≥ 2 | Section | Error | Client + server | "A choice section needs at least two questions" |
| Reducing a section's count below its locked questions | Section | Error | Client | "3 questions here are locked — unlock them first" |
| Reducing a count discards filled slots | Section | Warning + confirm | Client | "This removes 2 questions. Continue?" |

The locked-question interaction is a client-side rule with no server counterpart, and it is legitimate
because it is *more helpful*, not more restrictive: the server would accept the change and silently drop
locked content, which is precisely the outcome the lock exists to prevent (§13.8).

### 16.7 Difficulty and Bloom Distribution

| Rule | Scope | Severity | Evaluated | Message intent |
|------|-------|----------|-----------|----------------|
| Difficulty percentages sum to 100 | Blueprint | Error | Client + server | "Difficulty mix must total 100%" |
| Percentages non-negative and ≤ 100 | Blueprint | Error | Client + server | — |
| Target achievable given question counts | Blueprint | Warning | Client | "30% of 7 questions can't be met exactly" |
| Actual difficulty within tolerance of target | Paper | Warning | Client | §13.10 |
| Bloom percentages sum to 100 | Blueprint | Error | Client + server | "Bloom mix must total 100%" |
| Actual Bloom within tolerance | Paper | **Warning (advisory)** | Client | §13.11 |

**Why a tolerance exists.** With 7 questions, a 30/50/20 target is unachievable exactly — the closest
integers are 2/4/1 (29/57/14). Demanding exactness would make small sections permanently "invalid" for
arithmetic reasons that have nothing to do with paper quality. The tolerance is the smallest deviation
achievable at the section's question count, plus a small allowance.

**Why Bloom warns rather than errors** is argued in §13.11: the classification is interpretive and often
AI-assigned, so enforcing it would be false precision.

### 16.8 Upload Validation

| Rule | Scope | Severity | Evaluated | Message intent |
|------|-------|----------|-----------|----------------|
| Extension in the accepted set | File | Error | Client (advisory) + **server** | "Upload a PDF, DOCX, or image file" |
| MIME type matches the extension | File | Error | Client (advisory) + **server** | Same message |
| Size within the ceiling | File | Error | Client + server | "Files must be under 100 MB" |
| Page count within the limit | File | Error | Client where cheaply detectable + **server** | "Textbooks are limited to 800 pages" |
| Not empty / not zero-byte | File | Error | Client + server | "This file appears to be empty" |
| Not password-protected or corrupt | File | Error | **Server** (client detects only obvious cases) | "This PDF is password-protected" |
| Duplicate of an existing textbook | File | Warning | Server | "A textbook with this name and size already exists" |

**Client file checks are advisory only.** Extension and MIME type are attacker-controlled — a `.pdf`
extension and a declared `application/pdf` content type cost nothing to forge. The client checks them to
stop honest mistakes early and save a wasted upload; the server determines the true type by inspecting
content and re-validates every rule. The client's check is a UX optimization; the server's is the
control (§17.10, §24.12).

### 16.9 Approval and Template Validation

| Rule | Scope | Severity | Evaluated | Message intent |
|------|-------|----------|-----------|----------------|
| Paper passes the submit checklist | Paper | Error | Client + server | §13.17 |
| Paper is in a submittable state | Paper | Error | Client (projected) + **server** | "This paper is already awaiting approval" |
| Approver is not the author (BR-6) | Approval | Error | Client (projected, disabled-with-reason) + **server** | "You can't approve a paper you authored" |
| Paper unchanged since it entered the queue | Approval | Error | **Server** (409) | "This paper changed after it was submitted" |
| Comment present on reject / request-changes | Approval | Error | Client + server | "Explain what needs to change" |
| Template name unique within the org | Template | Error | Client (async, debounced) + **server** | "A template with this name already exists" |
| Template structurally complete | Template | Error | Client + server | "Every section needs a question type and count" |
| Template compatible with the target program | Apply | Warning | Client + server | "3 chapters aren't part of this program and won't carry over" |

The BR-6 row is the clearest illustration of projection versus enforcement (§7.1): the client disables
the control with an explanation, and the server rejects the request regardless. Neither substitutes for
the other.

### 16.10 Synchronous vs Asynchronous Validation

Async validation (uniqueness of a template name, an invite email) is debounced ≈500 ms, cancels
superseded requests via `AbortController`, and never blocks typing. Three disciplines apply:

| Discipline | Reason |
|-----------|--------|
| Never fire on every keystroke | It would hit the rate limiter (§10.12) and burn server capacity on values the user is still typing |
| Cancel superseded checks | Otherwise a slow response for "Mid" can arrive after "Midterm" and mark a valid name taken (§18.7) |
| Treat a failed check as *unknown*, not *invalid* | A network failure must not block a legitimate submit; the server will decide |
| Re-check on submit | The value may have been taken in the interim; async validation is advisory |

### 16.11 Cross-Field and Cross-Section Validation

Rules spanning fields live on the schema object that contains them: within a section (count, marks,
choice group) on the section schema; across sections (paper total, distribution) on the paper schema.
Errors attach to the most actionable field, or to the fieldset when they belong to no single input.
Evaluation is deferred to blur or submit for expensive rules (§15.4), and memoized per section so a
single edit does not recompute the whole document (§13.19).

### 16.12 Validating Data From the Server

The client parses every response envelope with Zod at the boundary (§10.6). When a response does not
match:

| Environment | Behaviour | Reason |
|-------------|-----------|--------|
| Development | Fail loudly — throw with the path and the received shape | The contract broke; a developer must see it immediately |
| Production | Degrade gracefully — render what is usable, treat missing optional data as absent, surface a scoped error if the data is essential | A shape change must not white-screen a teacher mid-paper |
| Both | Report to monitoring with `requestId`, endpoint, and the failing path | It is a contract defect and needs a ticket, not a silent catch |

Tolerant reading (unknown fields pass through) means additive backend changes never trigger this path
(§10.15); only genuinely breaking changes do.

### 16.13 Error Messages and EH Traceability

Copy guidelines: **specific** ("Section B declares 10 questions but has 8"), **actionable** (say what to
do), **non-blaming** (no "you failed to…"), and free of internal codes in the primary message. The
`error.code`, `ehRef`, and `requestId` are always available behind a details affordance so support can
correlate a report with a backend log (§25.9, §26.6).

| Validation domain | EH family mapping | Traceability note |
|-------------------|-------------------|-------------------|
| Field and schema validation | Validation family (`422`) | `error.details` carries field paths |
| Marks and structural rules | Validation family (`422`) | Mirrored client-side; disagreement is a monitored drift signal |
| Feasibility, quota, approval policy | Business-rule family (`400`) | Server-only; surfaced as a form-level alert with recovery |
| Concurrency | Conflict family (`409`) | Routed to the conflict flow, never to a field (§13.16) |
| Upload content rejection | Validation / business family | Server-determined true type; client check was advisory |

**Unattributable errors.** When `error.details` names a path the form does not expose, the message
degrades to a form-level alert rather than being dropped. Silently discarding a server error the user
cannot see is the worst possible outcome: the submit fails with no explanation.

### 16.14 Validation Performance

Full-schema validation on a 60-question paper is not free. It is contained by validating at the smallest
scope that can change (field on blur, section on section change), deferring whole-document rules to
submit, memoizing derived totals per section, and never validating on every keystroke except for the
cheap numeric feedback fields. The submit-time full validation is a single pass, and its cost is
irrelevant next to the generation it precedes.

### 16.15 Validation Design Decisions

| Decision | Choice | Why | Trade-off |
|----------|--------|-----|-----------|
| Library | Zod | One schema → runtime check + type + error map; also parses the API envelope (§10.6) | Schema maintenance; runtime parse cost |
| Authority | Mirror, never replace | The server is the only trustworthy validator; the client only speeds up feedback | Two implementations of some rules |
| Sync strategy | Shared schema package (or CI-generated schemas) | Drift becomes impossible rather than merely unlikely | Versioned coupling between deployables |
| Drift detection | Monitor unexpected `422`s | Turns an invisible degradation into a tracked signal | Requires the monitoring hook |
| Permissiveness | Client may be more permissive, never more restrictive | A client-only block leaves the user with no explanation and no recourse | Some failures only appear at submit |
| Severity model | Errors block, warnings inform | Distinguishes objective wrongness from pedagogical judgement | Requires a per-rule decision |
| Distribution tolerance | Smallest achievable deviation plus allowance | Integer counts make exact percentages impossible in small sections | A rule that must be explained in the UI |
| Bloom | Advisory only | The classification is interpretive and often AI-assigned | Two tolerance behaviours |
| File checks | Advisory client-side, authoritative server-side | Extension and MIME are trivially forged | An upload can still be rejected after transfer |
| Async checks | Debounced, cancellable, advisory | Avoids rate limits and stale-response races | Uniqueness can still fail at submit |
| Server shape errors | Loud in dev, graceful in prod, always reported | Developers must see it; users must not lose work | Requires environment-aware handling |

---

## 17. File Upload Architecture

### 17.1 What the frontend uploads

File transfer is not a general-purpose capability sprinkled across the product. It exists in a small number of well-understood places, and treating it as one shared mechanism rather than several bespoke ones is what keeps it reliable.

| Upload | Owner module | Typical size | Destination prefix | Post-upload work |
|---|---|---|---|---|
| Textbook source file (PDF, DOCX, scanned images) | Ingestion (§12.4) | 1 MB – 300 MB | `org/{orgId}/textbooks/{id}/raw/` | Long-running ingestion pipeline |
| Question attachment (diagram, figure image) | Question Bank (§12.3) | < 5 MB | `org/{orgId}/questions/{id}/assets/` | Thumbnail derivation only |
| Bulk question import file (CSV, XLSX) | Question Bank (§12.3) | < 20 MB | `org/{orgId}/imports/{id}/` | Parse, validate, staged review |
| Organisation branding asset (logo for exports) | Settings (§12.10) | < 2 MB | `org/{orgId}/branding/` | Validation and resize |
| User avatar | Profile (§12.12) | < 2 MB | `org/{orgId}/users/{id}/avatar/` | Resize |

Only the first two rows carry meaningful volume, and only the first produces a multi-minute pipeline afterwards. The architecture below is designed for the textbook case and used unchanged, minus the processing phase, for the others. Building the demanding case first and degrading gracefully is cheaper than building a simple uploader and retrofitting resumability into it later.

### 17.2 Why the API is never in the data path

The single most consequential decision in this section is that file bytes never traverse the FastAPI API. The browser obtains a short-lived signed URL from the API and then transfers the file **directly to object storage**.

| Consequence | Effect |
|---|---|
| API instances stay small and stateless | No request bodies measured in hundreds of megabytes, no tuning of body-size limits, no long-lived sockets consuming a worker slot |
| Horizontal scaling is unaffected by upload volume | Upload throughput becomes a property of object storage, which is built for it, rather than of the API tier |
| No double transfer | A proxying API would receive the file and then write it onward, doubling bandwidth and adding minutes of latency to large files |
| Resumability is available | Object storage multipart APIs support resume natively; a proxy would have to reimplement that state machine |
| Failure isolation | A stalled upload cannot exhaust API capacity and degrade unrelated requests such as paper generation |

The trade-off is honest and worth stating: the API loses the ability to inspect bytes as they arrive. Validation therefore happens **before** the transfer (through constraints baked into the signature) and **after** it (server-side inspection during the completion step and the ingestion pipeline), never during. §17.15 covers how the signature carries those constraints so the browser cannot widen them.

### 17.3 The three-step handshake

Every upload, regardless of module, follows the same three server interactions. The names below are the vocabulary used throughout the rest of this section.

```
  INITIALISE                 TRANSFER                    COMPLETE
  ----------                 --------                    --------
  POST .../uploads      →    PUT signed-url         →    POST .../uploads/{id}/complete
  (API, small JSON)          (object storage, bytes)     (API, small JSON)

  returns:                   returns:                    returns:
    uploadId                   ETag / part ETags           resource record
    signedUrl(s)               HTTP 200                    job id (if async)
    requiredHeaders                                        HTTP 202 for ingestion
    expiresAt (15 min)
    constraints echoed
```

The middle step is the only one that touches bytes and the only one that does not talk to the API. The two API calls are ordinary tenant-scoped REST requests and pass through the full dependency chain — `get_current_user → get_org_context → require_roles → scope` — exactly like any other endpoint. This matters: authorisation for an upload is decided at initialise time, by the server, using the `activeOrgId` claim. The client never chooses the destination path.

### 17.4 Upload sequence diagram

```
Teacher      Upload UI      uploads store     API            Object Storage     Worker
   |             |                |             |                   |              |
   |-- select -->|                |             |                   |              |
   |             |-- enqueue ---->|             |                   |              |
   |             |                |             |                   |              |
   |             |   [advisory client validation: type, size, name]  |              |
   |             |                |             |                   |              |
   |             |                |-- POST /textbooks/uploads ----->|              |
   |             |                |   {filename, size, mimeType}    |              |
   |             |                |             |                   |              |
   |             |                |   [quota check, role check,     |              |
   |             |                |    org scoping, signature mint] |              |
   |             |                |<-- 201 {uploadId, urls, TTL} ---|              |
   |             |                |             |                   |              |
   |             |<- phase=uploading            |                   |              |
   |<- progress -|                |             |                   |              |
   |             |                |-- PUT part 1 ------------------->|             |
   |             |                |-- PUT part 2 ------------------->|             |
   |             |                |   (progress events per part)     |             |
   |             |                |<-- 200 {ETag} -------------------|             |
   |             |                |             |                   |              |
   |             |<- phase=finalising           |                   |              |
   |             |                |-- POST /uploads/{id}/complete -->|              |
   |             |                |   {parts:[{n, etag}]}           |              |
   |             |                |             |                   |              |
   |             |                |   [verify object exists,        |              |
   |             |                |    real size/type inspection,   |              |
   |             |                |    create textbook row,         |              |
   |             |                |    enqueue ingestion job]       |              |
   |             |                |<-- 202 {textbookId, jobId} -----|              |
   |             |                |             |                   |              |
   |             |<- phase=processing           |-- job ----------------------------->|
   |<- SSE: textbook.progress ------------------|<-- Redis pub/sub -------------------|
   |<- SSE: textbook.parsed --------------------|<--------------------------------- |
   |             |                |             |                   |              |
   |             |<- phase=ready  |             |                   |              |
```

Two properties of this diagram carry design weight. First, the client's job ends at `complete`; everything after that is observed through SSE (§11), not polled. Second, the `202` response is the contract boundary — it means "accepted, durable, and queued", so the UI may safely navigate away or close the tab without losing the work.

### 17.5 Upload state machine

```
                    ┌──────────┐
                    │ selected │
                    └────┬─────┘
                         │ client checks
                    ┌────▼──────────┐   fail    ┌──────────┐
                    │  validating   ├──────────►│ rejected │
                    └────┬──────────┘           └──────────┘
                         │ ok
                    ┌────▼───────────┐  4xx/quota  ┌────────┐
                    │  initialising  ├────────────►│ failed │
                    └────┬───────────┘             └───┬────┘
                         │ signed URL                  │ retry
                    ┌────▼───────────┐  network        │
        pause ◄─────┤   uploading    ├─────────────────┘
        resume ────►└────┬───────────┘
                         │ all parts stored
                    ┌────▼───────────┐  409/422    ┌────────┐
                    │  finalising    ├────────────►│ failed │
                    └────┬───────────┘             └────────┘
                         │ 202 accepted
                    ┌────▼───────────┐  pipeline error  ┌──────────────────┐
                    │  processing    ├─────────────────►│ partially_ready  │
                    │  (SSE-driven)  ├─────────────────►│ processing_failed│
                    └────┬───────────┘                  └──────────────────┘
                         │ textbook.parsed
                    ┌────▼───────┐
                    │   ready    │
                    └────────────┘

  cancelled: reachable from validating, initialising, uploading, finalising
             (not from processing — see §17.11)
```

The state machine is deliberately explicit about the difference between `failed` (transfer or acceptance failed; the user's file is still on their disk and retry is local) and `processing_failed` (the file is durably stored and the server-side pipeline failed; retry is a server operation on an existing resource). Collapsing those two into one "error" state is the most common mistake in upload UIs, and it produces retry buttons that do the wrong thing.

### 17.6 Two distinct progress phases

Users perceive one operation. The system performs two, with radically different characteristics, and the UI must not pretend otherwise.

| Property | Phase 1 — Transfer | Phase 2 — Processing |
|---|---|---|
| What is happening | Bytes moving from browser to object storage | OCR, parse, structure, index (see [05 Textbook Ingestion](05_Textbook_Ingestion.md)) |
| Progress source | Browser upload progress events (bytes sent / total) | `textbook.progress` SSE events with a server-reported stage |
| Accuracy | Exact and monotonic | Stage-based estimate; percentage is advisory |
| Duration | Proportional to file size and the user's uplink | Proportional to page count and OCR need; minutes |
| Requires the tab | Yes | No |
| Cancellable | Yes | Only by cancelling the job, if the pipeline supports it |
| Failure meaning | Local retry | Server-side retry on a stored resource |

The interface renders these as two segments with distinct labels rather than one blended bar, because a single 0–100% bar that stalls at 60% for four minutes reads as a hang. The transfer segment is precise; the processing segment shows the current stage name, which is honest about being coarse:

```
  physics-class-11.pdf   ·   84 MB

  Transfer     ████████████████████████████  100%   done in 1m 12s
  Processing   ██████████░░░░░░░░░░░░░░░░░░   Structuring (3 of 4)
               OCR ✓   Parse ✓   Structure ●   Index ○

  You can close this page. We'll notify you when it's ready.
```

That last line is not decoration. It is the visible consequence of the `202` contract in §17.4, and it is what makes the multi-minute wait acceptable.

### 17.7 Chunked and resumable transfer

Files above a threshold are uploaded as multiple parts against a multipart signature set rather than a single `PUT`.

| Concern | Decision | Reasoning |
|---|---|---|
| Threshold | Single `PUT` below ~8 MB, multipart above | Below the threshold the coordination overhead exceeds the benefit; a failed small upload is cheap to repeat |
| Part size | Fixed, in the 5–16 MB range | Object storage imposes a minimum part size; larger parts mean fewer round trips but a costlier retry unit |
| Concurrency | A small bounded number of parts in flight (2–4) | Saturates a typical uplink without starving the SSE connection or interactive requests (§11.3) |
| Retry unit | The individual part | A 300 MB upload must never restart from zero because part 14 of 30 failed |
| Resume across reloads | Supported when the upload session is still valid | The `uploadId` and stored part ETags live server-side; the browser re-requests signatures for the missing parts |
| Resume across file re-selection | Requires the user to pick the same file again | Browsers cannot re-read a `File` handle after a reload; this is a platform constraint, not a design choice, and the UI states it plainly |
| Integrity | Per-part ETag returned to `complete`; server verifies the assembled object | Detects truncation and corruption before the pipeline consumes the file |

Resume is where the signed-URL model earns its keep. Because part state is held by object storage and referenced by `uploadId`, a resumed upload needs only fresh signatures for the outstanding parts — the API does not have to remember anything about the bytes.

### 17.8 The `uploads` store

Upload state is client-only, cross-route, and not server-owned until `complete` succeeds, which places it squarely in Zustand rather than the query cache (§9.2). The `uploads` store (§9.3) holds one entry per upload with its phase, byte counters, part table, error, and cancellation handle.

| Property | Behaviour |
|---|---|
| Queue depth | Bounded; a small number of concurrent uploads (2–3), the rest queued |
| Why bounded | Unbounded parallelism collapses per-file throughput, starves interactive requests, and makes progress reporting meaningless |
| Ordering | FIFO, with the currently transferring items pinned to the top of the UI |
| Route independence | An upload started on the ingestion page continues while the user navigates to the paper builder |
| Global surface | A compact tray in the app shell (§8) shows active uploads from any route |
| Lifetime | Entries survive navigation; they do not survive a reload (see §17.7 on `File` handles) or logout teardown (§9.8) |
| Org switch | All uploads are cancelled by the hard reset (§9.7) — a signature minted for one `org_id` must never be used after switching |
| Never holds | File bytes beyond the active `File` references, server entity data, or anything derivable from the query cache |

When `complete` returns, ownership transfers: the store marks the entry `processing` and the resulting resource becomes query-cache state, updated by SSE like any other server entity. The store entry then exists only to keep the tray honest until the resource reaches `ready`.

### 17.9 Client-side validation is advisory

The client checks file extension, reported MIME type, size, and filename sanity before initialising. All four are advisory, for the reason given in §16.8: extension and MIME are attacker-controlled strings, and size can be misreported by a non-browser client. Their value is latency — a user who picked the wrong file learns in 50 ms instead of after a 200 MB transfer.

| Check | Client behaviour | Server behaviour |
|---|---|---|
| Extension / declared type | Blocks selection with a clear message listing accepted types | Constrains the signature by content type; inspects real magic bytes at `complete` |
| Size | Blocks selection above the plan limit; warns near it | Signature carries a content-length range; oversize transfers are rejected by storage itself |
| Filename | Trims, normalises, warns on unusual characters | Generates the storage key itself; the user's filename is stored as metadata only |
| Page count / content shape | Not knowable client-side | Determined during ingestion; may still fail after a successful upload |
| Quota | Shows remaining quota before the user starts | Authoritative check at initialise; a `400` with an `ehRef` if exceeded |

The last row is important for perceived quality: checking quota **before** the transfer rather than at `complete` means the user is never told they are out of quota after waiting six minutes. This is the upload instance of the general rule in §12.9 — quota before commitment.

### 17.10 Retry policy per phase

Retry is phase-specific because the failure modes are unrelated.

| Failure | Retry | Backoff | User-visible |
|---|---|---|---|
| Initialise — network or `5xx` | Automatic, bounded (3) | Exponential with full jitter | Only if all attempts fail |
| Initialise — `400` quota, `403`, `422` | Never | — | Immediate, with the `ehRef` message |
| Transfer — part network error or `5xx` | Automatic per part, bounded (4) | Exponential with full jitter | Part-level retries are silent; the bar keeps its position |
| Transfer — signature expired (`403` after TTL) | Automatic: re-initialise signatures, resume outstanding parts | Immediate, once | Silent unless re-signing fails |
| Transfer — offline | Pause, do not consume retries | Resume on `online` | "Paused — waiting for connection" |
| Complete — network or `5xx` | Automatic, bounded (3); **safe because the endpoint is idempotent on `uploadId`** | Exponential with full jitter | Only if all attempts fail |
| Complete — `409` already completed | Treat as success | — | None; reconcile to the existing resource |
| Complete — `422` integrity or type mismatch | Never automatically | — | Explicit failure with the reason; retry means re-upload |
| Processing — pipeline failure | Server-side retry of the job, not the upload | Server policy | `textbook.failed` event; a "Retry processing" action on the stored resource |

The `complete` row deserves emphasis. It is a mutation, and §10.9 forbids blind retries of non-idempotent mutations. It is retryable here specifically because it is keyed on `uploadId` and idempotent by design. That is a backend contract the frontend depends on, and it is the reason a flaky network at the tail of a large upload does not lose the file.

### 17.11 Cancellation

| Phase | Cancellable | What happens |
|---|---|---|
| `validating` / `initialising` | Yes | Abort the in-flight request; drop the store entry |
| `uploading` | Yes | Abort part requests; call the abort endpoint so storage discards accumulated parts |
| `finalising` | Yes, with a caveat | The `complete` call may have already succeeded server-side; the client reconciles and, if the resource exists, offers deletion instead |
| `processing` | Only if the pipeline supports job cancellation | The file is durably stored; cancelling means deleting the resource, which is a distinct, confirmed, destructive action |
| `ready` | Not cancellation | Deletion of a real resource, governed by role and confirmation rules |

Cancelling during `uploading` explicitly notifies the server so multipart state is released rather than left to a lifecycle rule. Silent abandonment works, but it bills the tenant for storage they never received value from and leaves the orphan case below to clean up.

### 17.12 Orphaned objects

Three sequences produce bytes in object storage with no corresponding database row:

| Sequence | Detection | Resolution |
|---|---|---|
| Transfer succeeded, browser closed before `complete` | No row references the key | Storage lifecycle rule expires incomplete multipart uploads and unreferenced keys under a staging prefix |
| Transfer succeeded, `complete` returned `422` | The API knows the key and the rejection | The API deletes the object as part of rejecting it |
| Multipart abandoned mid-transfer | Incomplete multipart upload record | Lifecycle rule aborts incomplete uploads after a fixed window |

The frontend's only obligation is to call `complete` or the abort endpoint whenever it can, and to prefer the abort endpoint over silence on explicit cancellation. Everything else is a storage-lifecycle concern, documented here so the boundary is unambiguous: **the client is not responsible for cleanup, and must not be trusted with it**, because the client is exactly the component that disappears unexpectedly.

### 17.13 Download and signed reads

The mirror image of upload — downloading an exported paper or viewing an ingested source page — uses the same principle with a shorter-lived signature.

| Aspect | Decision |
|---|---|
| URL lifetime | 1 hour for downloads, versus 15 minutes for uploads |
| Why longer | A download URL is often clicked minutes after the list was rendered; an upload signature is consumed immediately |
| Minting | On demand, when the user activates the download — never eagerly for every row in a list |
| Why not eager | Eager minting creates hundreds of live capability URLs per page render, most never used, all leakable through logs and browser history |
| Delivery | Navigate to the signed URL, or fetch and stream to a blob when a filename or progress is needed |
| Expired URL | The `403` is caught and the client re-mints transparently, then retries once |
| Sharing | Signed URLs are treated as secrets: never rendered as visible link text, never copied to the clipboard by a "share" affordance |

That last point is a genuine security consideration and is expanded in §24: a signed URL is a bearer capability. Anyone holding it has the access it encodes, regardless of tenant or role, until it expires.

### 17.14 Failure handling summary

| Failure | User-visible behaviour | Data outcome |
|---|---|---|
| Wrong file type chosen | Blocked at selection with the accepted list | Nothing sent |
| File too large for plan | Blocked at selection; quota panel links to billing | Nothing sent |
| Quota exhausted | Initialise fails with the mapped `ehRef` message | Nothing stored |
| Uplink drops mid-transfer | Upload pauses, resumes automatically when online | Completed parts retained |
| Signature expires mid-transfer | Silent re-sign and resume | No visible interruption |
| Tab closed mid-transfer | Upload is lost; the file must be re-selected | Parts expire via lifecycle rule |
| Tab closed after `complete` | Nothing lost; processing continues server-side | Resource exists; SSE and notifications report the outcome |
| `complete` rejects on integrity | Explicit failure naming the reason | Object deleted server-side |
| Pipeline fails on a valid file | `textbook.failed`; resource shows `processing_failed` with a retry action | File retained for retry |
| Pipeline partially succeeds | `partially_ready`; usable chapters listed, failed ones flagged (§12.4) | Partial data retained |
| Storage unreachable | Transfer retries, then fails with a distinctly worded infrastructure error | Nothing stored |

The through-line matches §13.20: **no failure path silently discards work the user has already paid time for.** Where work genuinely cannot be preserved — a closed tab mid-transfer — the UI says so before the fact, in the tray, rather than after.

### 17.15 Security considerations

| Risk | Mitigation |
|---|---|
| Client dictating the storage path | The client never sends a path. The server derives the key from `activeOrgId` and the resource id, so a compromised client cannot write outside its tenant prefix |
| Signature over-scoping | Signatures are per-object, single-method (`PUT`), content-type constrained, content-length constrained, and expire in 15 minutes |
| Signature reuse after org switch | The hard reset (§9.7) cancels all uploads and discards all signatures |
| Malicious file content | The API inspects magic bytes at `complete`; the pipeline runs untrusted parsing in isolated workers, never in the API process |
| Filename injection into export headers or paths | The user's filename is metadata only; the storage key and any `Content-Disposition` value are server-generated and escaped |
| Signed URL leakage | Short TTL; never logged by the client's telemetry hook (§10.16); never placed in visible link text or the clipboard |
| CSRF on `complete` | The completion call uses the in-memory bearer token, not a cookie, so it is not forgeable cross-site (§24) |
| Cross-tenant read via a guessed key | Object storage denies unsigned access entirely; keys are not a security boundary, signatures are |
| Zip/archive expansion abuse | Archive handling, if introduced, is a server-side pipeline concern with expansion limits — never a client capability |

### 17.16 Performance considerations

| Technique | Effect |
|---|---|
| Direct-to-storage transfer | Removes an entire proxy hop and the API's bandwidth ceiling from the critical path |
| Bounded part concurrency | Maximises effective throughput without starving the SSE stream and interactive fetches |
| Progress coalesced to animation frames | A 300 MB multipart upload emits thousands of progress events; rendering each one is the dominant cost in a naive implementation (§11.16) |
| Store updates via selectors | Progress ticks re-render the tray and the active row only, not the page |
| No file content in React state | `File` and `Blob` references are held in the store, never spread into props or serialised |
| Thumbnails derived server-side | The browser never decodes a 300 MB PDF to show a preview |
| Lazy-loaded uploader | The multipart machinery and any preview libraries load on first use, not in the shell bundle (§19) |

### 17.17 Extensibility

| Future capability | How the architecture accommodates it |
|---|---|
| Drag-and-drop folder ingestion | The queue already models many concurrent uploads; folder traversal adds items, not a new mechanism |
| Import from cloud storage providers | Becomes a server-side transfer: initialise returns a job instead of signatures, and the UI drops straight to `processing` |
| Virus scanning | Inserts a phase between `finalising` and `processing`, reported through the same SSE stage vocabulary |
| Client-side pre-processing (page-range selection, downscaling) | Fits between `validating` and `initialising`; the transferred object is what the client produced |
| Resumable across sessions with a file-system handle | Where the platform allows re-acquiring a handle, resume graduates from best-effort to reliable without changing the server contract |

### 17.18 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Data path | Direct browser → object storage via signed URL | Keeps the API stateless and small; storage handles what it is built for | The API cannot inspect bytes in transit |
| Handshake | Initialise → transfer → complete | Authorisation and quota are decided by the server before any bytes move | Three round trips instead of one |
| Signature TTL | 15 min upload, 1 h download | Matches how long each is realistically held before use | Long transfers need transparent re-signing |
| Large files | Multipart with per-part retry | A failure late in a 300 MB upload must not restart from zero | Coordination and part-state complexity |
| Progress model | Two labelled phases, never one blended bar | A stalled single bar reads as a hang; the phases have genuinely different accuracy | Slightly more UI |
| `complete` idempotency | Required, keyed on `uploadId` | Makes the tail of a large upload safe to retry | A backend contract the frontend depends on |
| Failure taxonomy | `failed` (retry locally) vs `processing_failed` (retry server-side) | The correct retry action differs completely | Two error states to design |
| Client validation | Advisory only | Type and size are attacker-controlled; only the server can be authoritative | An upload can be rejected after transfer |
| Queue | Bounded concurrency, FIFO, route-independent | Preserves throughput and interactivity; uploads survive navigation | Uploads do not survive reload |
| Orphan cleanup | Storage lifecycle rules, plus explicit abort when possible | The client is precisely the component that vanishes unexpectedly | Some transient storage cost |
| Download URLs | Minted on activation, never eagerly per row | Avoids scattering hundreds of unused bearer capabilities | One extra request per download |

---

## 18. Search and Filtering Architecture

### 18.1 One mechanism, many surfaces

Search, filtering, sorting, and pagination are one concern with one implementation. Every list surface in the product — question bank, papers, textbooks, templates, users, audit log, notifications — composes the same primitives against the same query-parameter contract. The alternative, per-page bespoke filtering, produces divergent URL formats, inconsistent empty states, and a different stale-response bug on every screen.

| Surface | Free text | Facets | Default sort | Pagination | Notes |
|---|---|---|---|---|---|
| Question bank (§12.3) | Yes, plus semantic | Rich: subject, chapter, type, difficulty, Bloom, marks, source, status | Relevance when searching, else `-created_at` | Cursor | The most demanding surface; drives the design |
| Papers (§12.5) | Title | Status, program, class, author, date range | `-updated_at` | Offset | Mutating list; small per user |
| Textbooks (§12.4) | Title | Status, subject, class | `-created_at` | Offset | Small cardinality |
| Templates (§12.6) | Name | Scope, subject, class | Name ascending | Offset | Small |
| Approvals (§12.7) | Paper title | Status, submitter, date range | Oldest submission first | Offset | A work queue, so oldest-first |
| Users and memberships (§12.10) | Name, email | Role, status | Name ascending | Offset | Admin only |
| Audit log (§12.10) | Actor, resource | Action, actor, resource type, date range | `-occurred_at` | Cursor | Append-only and large |
| Notifications (§12.11) | No | Read state, type | `-created_at` | Cursor | Grows continuously |

### 18.2 The query contract

The parameters are fixed by [07 Backend Architecture](07_Backend_Architecture.md) and used verbatim:

| Parameter | Form | Notes |
|---|---|---|
| Free text | `q=photosynthesis` | Trimmed, collapsed whitespace; empty means absent, never `q=` |
| Facets | `filter[difficulty]=medium&filter[type]=mcq` | Repeated keys mean OR within a facet; different keys mean AND across facets |
| Ranges | `filter[marks_min]=2&filter[marks_max]=5` | Explicit bounds rather than encoded range syntax |
| Sort | `sort=-created_at,title` | Leading `-` is descending; multiple keys are comma-separated and ordered |
| Offset pagination | `page=2&pageSize=20` | Default 20, maximum 100 |
| Cursor pagination | `cursor=<opaque>&pageSize=20` | The cursor is opaque; the client never constructs or parses one |

Two client-side rules keep this contract usable as a cache key. First, **omit rather than send empty**: an absent filter and an empty-string filter must not produce two cache entries for the same result set. Second, **normalise before keying**: facet values are sorted, whitespace is collapsed, and defaults are dropped, so `?q=algebra&filter[type]=mcq` and `?filter[type]=mcq&q=algebra%20` resolve to one canonical descriptor.

### 18.3 From URL to cache key

```
  ┌─────────────────────────────────────┐
  │  URL search params (source of truth)│
  └──────────────┬──────────────────────┘
                 │ parse + Zod-validate (§16.12)
  ┌──────────────▼──────────────────────┐
  │  ListQuery descriptor (typed)       │   invalid or unknown params are
  │  { q, filters, sort, page, cursor } │   dropped, not fatal — a stale
  └──────────────┬──────────────────────┘   bookmark must still load
                 │ normalise (sort keys, drop defaults, trim)
  ┌──────────────▼──────────────────────┐
  │  canonical descriptor               │
  └──────────────┬──────────────────────┘
                 ├──────────────► query key: [orgId,'questions','list',desc]
                 └──────────────► request params: q, filter[…], sort, page
```

The descriptor is the single artefact that becomes both the cache key and the request. Deriving both from one normalised object is what guarantees that two UI states which mean the same thing share a cache entry — and that two which differ never collide. Query keys remain org-first (§9.4), so a filtered question-bank result for one tenant can never be served to another.

### 18.4 URL as the source of truth

List state lives in the URL, not in a store.

| Benefit | Consequence |
|---|---|
| Shareable and bookmarkable | A teacher can send an admin the exact filtered view they are asking about |
| Reload-safe | Refreshing a filtered list is not destructive |
| Back button behaves | Navigation history matches user intent |
| No duplicate state | The store cannot disagree with the URL, because there is no store copy |
| Server-renderable | The initial page can be prepared from the request URL |

The counterweight is history pollution: naive synchronisation pushes a history entry per keystroke, so one search leaves fifteen entries and Back becomes unusable.

| Interaction | History behaviour |
|---|---|
| Typing in the search box | **Replace** — after the debounce, and only once per settled value |
| Toggling a facet | Push — a deliberate, discrete decision |
| Changing sort | Push |
| Changing page | Push |
| Clearing all filters | Push |
| Opening a row's detail panel | Push, so Back closes the panel |
| Restoring a saved view | Push |

Pagination resets to page one whenever `q`, any facet, or the sort changes. Preserving `page=7` across a filter change is a reliable way to show an empty list for a query that has plenty of results.

### 18.5 Debounce, cancellation, and the stale-response race

```
  keystroke  p  h  o  t  o  s  y  n
             │  │  │  │  │  │  │  │
             └──┴──┴──┴──┴──┴──┴──┴─► debounce 300 ms
                                       │
                            ┌──────────▼─────────┐
                            │ request "photosyn" │
                            └──────────┬─────────┘
                                       │
             earlier in-flight requests are cancelled,
             and any that still resolve are discarded
             because the cache key no longer matches
```

| Concern | Decision | Reasoning |
|---|---|---|
| Debounce window | ~300 ms for text | Below ~200 ms the server sees per-keystroke load; above ~400 ms typing feels laggy |
| Facets and sort | No debounce | A click is already a settled intention |
| Minimum length | 2 characters for text search; below that, text is ignored rather than sent | One-character searches return nearly everything and cost the most |
| Cancellation | Superseded requests are aborted (§10.13) | Frees server capacity and the browser's connection budget |
| Stale responses | Structurally impossible | A late response lands under its own key; the UI reads the key it currently cares about. This is the decisive advantage of keying on the descriptor rather than storing "the current results" |
| Keeping the previous page visible | Previous data is shown while the next query resolves, with a subtle pending indicator | Prevents the list from collapsing to a spinner on every keystroke |
| Empty vs pending | Distinguished explicitly | "No results" and "still looking" must never look alike |

The stale-response row is worth dwelling on. In a store-based design, results for `"photo"` arriving after results for `"photosyn"` overwrite the correct list, and the bug is intermittent and load-dependent. Keying every result set by its own canonical descriptor removes the race by construction rather than by patching it with request sequence numbers.

### 18.6 Offset versus cursor pagination

| Aspect | Offset (`page`) | Cursor |
|---|---|---|
| Used for | Small, stable, admin-scale lists | Large or continuously mutating lists |
| Can jump to page N | Yes | No |
| Total count | Available and shown | Usually approximate or omitted |
| Behaviour under concurrent inserts | Items shift between pages; duplicates and skips appear | Stable relative to the cursor |
| Cost at depth | Degrades as the offset grows | Constant |
| Infinite scroll | Unsafe — accumulating offset pages duplicates and skips rows | The correct substrate |

The operational rule: **infinite scroll requires cursors.** The question bank uses cursor-based infinite scroll because it is large, frequently appended to by ingestion, and browsed by scanning rather than by page number. Admin tables use offset pagination because "page 3 of 7" and a real total are more useful there than smooth scrolling, and the lists are small enough that shifting is rare and harmless.

| Surface | Mode | Why |
|---|---|---|
| Question bank | Cursor, infinite scroll | Large, append-heavy, scanned rather than paged |
| Audit log, notifications | Cursor, "load more" | Append-only and unbounded; explicit loading beats auto-loading through history |
| Papers, textbooks, templates, users | Offset, numbered pages | Small and stable; totals and page jumps are genuinely useful |
| Paper builder question picker | Cursor, infinite scroll | Behaves like the question bank inside a dialog |

### 18.7 Facet architecture

Facets are the question bank's primary interaction, so they get explicit treatment.

| Concern | Decision | Reasoning |
|---|---|---|
| Facet definitions | Server-provided, per org and program | Available subjects, chapters, and question types are tenant data, not constants |
| Counts | Server-computed alongside results, reflecting all other active facets | A facet count that ignores the rest of the query misleads users into empty selections |
| Zero-count options | Shown disabled rather than hidden | Disappearing options make the panel feel unstable and hide the vocabulary of the corpus |
| Dependent facets | Chapter options narrow to the selected subjects | Prevents impossible combinations rather than reporting them |
| Multi-select semantics | OR within a facet, AND across facets | The conventional and least surprising model |
| Applied-filter display | A chip row above results with individual and bulk clear | The URL is authoritative but not readable; the chips are the readable projection |
| Cost control | Counts requested with the result page, not per facet | One query, not fifteen |
| Cascading refresh | Facet counts refetch with the result set under the same descriptor | Counts and rows are always consistent with each other |

```
  ┌───────────────────────┬──────────────────────────────────────────┐
  │ FILTERS               │  q: "photosynthesis"                     │
  │                       │  ┌────────────────────────────────────┐  │
  │ Subject               │  │ chips: Biology ×  MCQ ×  Clear all │  │
  │  ☑ Biology      412   │  └────────────────────────────────────┘  │
  │  ☐ Chemistry    128   │                                          │
  │  ☐ Physics        0   │  318 questions · sorted by relevance     │
  │                       │  ─────────────────────────────────────   │
  │ Chapter               │  □ Explain the light-dependent reaction  │
  │  ☐ Ch 6 – Photo  204  │      4 marks · medium · Understand       │
  │  ☐ Ch 7 – Resp    98  │      from Textbook · Bio XI · Ch 6       │
  │                       │  ─────────────────────────────────────   │
  │ Type · Difficulty     │  □ Define photolysis                    │
  │ Bloom · Marks         │      1 mark · easy · Remember            │
  │ Source · Status       │      AI-generated · reviewed             │
  └───────────────────────┴──────────────────────────────────────────┘
```

### 18.8 Text, semantic, and hybrid search

The question bank supports more than substring matching. The retrieval capabilities in [06 AI Architecture](06_AI_Architecture.md) surface here as search modes.

**Availability note.** Semantic search depends on embeddings, which are **deferred** at v1
([ADR-007](ADR/ADR-007-defer-pgvector-retrieval.md)) — MVP retrieval is a metadata filter plus
`tsvector` full-text and `pg_trgm` trigram ranking. The client therefore treats the mode list as
**server-advertised capability, not a hardcoded assumption**: the search surface reads the available
modes from the API's capability response and renders only those. When embeddings are adopted, the
semantic and hybrid modes appear without a frontend release. Hardcoding a "Semantic" toggle that
returns lexical results would be a UI that lies about what the system did.

| Mode | Availability | What it matches | Frontend responsibility |
|---|---|---|---|
| Lexical (default at v1) | Always | Words and phrases present in the text, plus fuzzy trigram matches | Send `q`; render matched terms as highlights |
| Semantic | Only when the API advertises it | Conceptually related content without shared wording | Send `q` with the mode flag; render a relevance indicator, not a false exact-match highlight |
| Hybrid | Only when the API advertises it | Blended lexical and semantic ranking | Treat ranking as server-owned and never re-sort client-side |

Three rules govern this surface. **Ranking is never recomputed on the client** — reordering a returned page by a local heuristic breaks pagination and misrepresents relevance. **Relevance sort is only offered while `q` is present**, and clearing the search falls back to the surface's default sort. **Semantic results are labelled as such**, because a result with no visible term overlap looks like a bug unless the interface explains why it is there.

Semantic search is also slower and costlier than lexical search. It therefore has a longer debounce, a higher minimum query length, and a stricter staleness window than the 15–30 s used for lexical search results (§9.5), and it is never fired on a partially typed word.

A fourth rule follows from the availability note: **the client never claims a capability the server has not advertised.** If the modes list contains only `lexical`, the mode selector is not rendered at all — an inert control is worse than an absent one.

### 18.9 Saved views and recent searches

| Kind | Storage | Scope | Reasoning |
|---|---|---|---|
| Saved views (named filter sets) | Server, tenant-scoped | Shared or private per the creator's choice | A curated view is real work; it must survive devices and be shareable (§9.9) |
| Recent searches | `localStorage` | Per device | Convenience only; losing them costs nothing and syncing them costs a round trip |
| Last-used filters per surface | Not persisted by default | — | Silently reapplying yesterday's filters makes an empty list look like missing data |

Saved views are stored as canonical descriptors, not as URLs, so a view keeps working when a route changes. Applying one pushes a history entry and replaces the entire query state rather than merging into it — merging produces combinations the author never intended.

### 18.10 Selection across a filtered set

Bulk actions on a filtered list contain a trap that is worth naming precisely, because it is the difference between tagging 40 questions and tagging 4,000.

| Selection kind | Meaning | Sent to the server as |
|---|---|---|
| Select loaded | The rows currently materialised in the client | An explicit list of ids |
| Select all matching | Every row the query matches, including rows never loaded | The canonical filter descriptor |

The UI must distinguish these unmistakably. Checking the header checkbox selects the loaded rows and then offers an explicit second step: "All 40 on this page are selected. Select all 3,182 matching questions?" The count comes from the server, the confirmation dialog restates the filter in words, and the mutation sends the descriptor rather than an id list. Sending 3,182 ids is fragile and, worse, is silently wrong for any row that appeared after the page was rendered — the filter itself is the honest expression of intent. This mirrors the filter-based bulk actions described in §12.3.

### 18.11 Accessibility

| Concern | Behaviour |
|---|---|
| Result count changes | Announced via a polite live region: "318 questions found" — debounced so typing does not produce a stream of announcements |
| Loading state | Announced once when a search begins, not per re-render |
| Facet panel | A labelled group of grouped checkboxes; counts are part of each accessible label, so a screen reader hears "Biology, 412 results" |
| Applied-filter chips | Each chip's remove control names what it removes: "Remove filter Biology" |
| Keyboard | Full traversal of the facet panel; Escape returns focus from the panel to the search field |
| Infinite scroll | Paired with an explicit "Load more" control, because scroll-triggered loading is unreachable by keyboard alone and disorienting with a screen reader |
| Focus on page change | Moves to the top of the result region, never lost to the document body |
| Zero results | A real message in the result region, not an empty container |

The infinite-scroll row is a genuine constraint, not a nicety: an intersection-observer-only implementation has no keyboard equivalent. The observer is treated as an enhancement over a control that always exists.

### 18.12 Performance

| Technique | Effect |
|---|---|
| Descriptor-keyed caching | Returning to a previous filter combination is instant and requires no request |
| `keepPreviousData` semantics | The list never collapses to a skeleton between keystrokes |
| Short staleness for search results (15–30 s) | Fresh enough for an evolving corpus, long enough that back-navigation is free (§9.5) |
| Facet counts in the same response | One request per query instead of one per facet |
| Row virtualisation above ~100 rows | Keeps a 3,000-row scroll list at a constant DOM size |
| Memoised row components | A progress tick or hover elsewhere does not re-render 200 rows |
| Prefetch on hover for the next page | Hides latency on the most likely next action |
| Server-side filtering only | Client-side filtering of a paginated set is wrong, not merely slow — it filters one page and reports it as the whole result |
| Cancelled superseded requests | Server capacity is spent on the query the user actually wants |

### 18.13 Failure handling

| Failure | Behaviour |
|---|---|
| Search request fails (`5xx`) | Previous results remain visible with a non-destructive inline error and a retry control; filters are not cleared |
| Search times out | Treated as a failure, not as zero results — the distinction matters because zero results implies the corpus is empty |
| Facet metadata fails but results succeed | Results render; the facet panel shows its own error and retry, so one failure does not blank the screen |
| Semantic search unavailable | Silent fallback to lexical, with a small honest note that ranking is degraded |
| Invalid params in a bookmarked URL | Unknown or malformed params are dropped, valid ones applied, and the user is told what was ignored |
| Cursor invalidated or expired | Restart from the first page of the same filter set with an explanation, rather than showing an empty page |
| Rate limited (`429`) | Debounce widens, the pending indicator persists, and the retry honours `Retry-After` (§10.14) |
| Filter yields nothing | Zero-state names the active filters and offers to clear the most restrictive one |
| Offline | The last cached result set is shown, labelled as offline; new searches are blocked with an explanation rather than failing silently |

### 18.14 Extensibility

| Future capability | How the architecture accommodates it |
|---|---|
| Saved-search subscriptions ("notify me when new questions match") | A saved descriptor plus a notification type; the descriptor is already the persisted unit |
| Cross-entity global search | A new surface consuming the same descriptor and normalisation primitives with a type facet |
| Natural-language filtering ("hard 5-mark questions from chapter 6") | Parsed server-side into a descriptor, then applied through the existing path and shown as ordinary chips the user can edit |
| Column-configurable admin tables | Column choice is view state that joins the descriptor and rides along in saved views |
| Export of a filtered set | The descriptor is already the server-side expression of "these rows" (§18.10) |

### 18.15 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Implementation | One shared search and filter mechanism | Prevents divergent URL formats and a per-page stale-response bug | Requires a general abstraction up front |
| Source of truth | The URL | Shareable, reload-safe, back-button correct, no duplicate state | Requires disciplined push-vs-replace rules |
| History while typing | Replace, not push | One search would otherwise leave a dozen history entries | Intermediate queries are not individually revisitable |
| Cache keys | Canonical normalised descriptor, org-first | Equivalent UI states share a cache entry; different ones never collide | Normalisation must be centralised and tested |
| Stale responses | Prevented by key structure, not sequence numbers | Removes a whole class of intermittent, load-dependent bugs | Depends on never storing "current results" in a store |
| Filtering location | Server, always | Client-side filtering of a page misrepresents the result set | Every filter change is a request |
| Pagination | Cursor for large or mutating lists, offset for small stable ones | Offset breaks under concurrent inserts; cursors cannot jump to page N | Two modes to support |
| Infinite scroll | Only over cursors, always with a "Load more" control | Offset-based infinite scroll duplicates and skips rows; observers are not keyboard-reachable | Slightly more UI than a bare observer |
| Facet counts | Server-computed with the results, respecting other facets | Counts that ignore the rest of the query mislead users | Heavier query on the server |
| Zero-count facets | Disabled, not hidden | A stable panel is more legible than a shifting one | Longer facet lists |
| Ranking | Server-owned, never re-sorted client-side | Local re-sorting breaks pagination and misstates relevance | Relevance is opaque to the client |
| Saved views | Server-persisted descriptors | Real curation work must survive devices and be shareable | An admin surface to manage them |
| Recent searches | Device-local | Pure convenience; syncing is not worth a round trip | Not portable |
| Bulk selection | "All matching" sends the descriptor, not an id list | Ids are fragile and silently wrong for rows added after render | Requires an explicit, counted confirmation step |

---

## 19. Performance Optimization

### 19.1 What "fast" means for this product

Generic web-performance advice optimises for a marketing page's first paint. This application is a work tool used for long sessions by authenticated users, so the metrics that matter are only partly the standard ones.

| Metric | Why it matters here | Target |
|---|---|---|
| TTFB on authenticated routes | Every route runs the dependency chain and at least one query; a slow TTFB compounds across a session | < 400 ms p75 |
| LCP on the dashboard | The first impression of the product each morning | < 2.0 s p75 |
| INP across the app | Teachers interact constantly — typing, toggling, reordering | < 200 ms p75 |
| CLS on list and builder views | Late-arriving content shifting a question card under the cursor is actively harmful | < 0.1 |
| Time to interactive builder | Opening a draft paper is the core loop; this is the metric the product lives on | < 1.5 s p75 from click to editable |
| Time to first question card during generation | Perceived generation speed depends on the first card, not the last | < 5 s p75 after submit |
| Search keystroke-to-results | The question bank is browsed by typing | < 500 ms p75 including network |
| Reorder frame time | Drag interactions must not drop frames | 60 fps sustained |
| Route transition (cached) | Navigating between paper list and detail should feel instant | < 100 ms |

Two of these — time to interactive builder and time to first question card — are product metrics rather than web metrics, and they are the ones tracked most closely (§26). A page can pass every Core Web Vital and still feel slow if opening a paper takes four seconds.

### 19.2 Rendering strategy per route

Next.js offers several rendering modes; choosing per route rather than globally is the first and largest performance lever.

| Route group | Mode | Reasoning |
|---|---|---|
| Marketing and legal `(public)` | Static, cached at the edge | Identical for everyone; no reason to compute per request |
| Login, register, invitation accept | Static shell, client-side interaction | The form is static; only the submission is dynamic |
| Dashboard | Server-rendered shell with streamed widgets | The shell is instant; each widget resolves independently (§12.2) |
| List routes (papers, question bank, textbooks) | Server-rendered first page, client-owned thereafter | The first page benefits from server data; filtering is inherently client-driven (§18) |
| Detail routes (paper, textbook, question) | Server-rendered with streamed secondary panels | The primary record is what the user came for; history and analytics can arrive late |
| Paper builder | Server-rendered draft, fully client-interactive | The document model must be in the browser before the first edit (§13.3) |
| Analytics | Server-rendered with streamed charts | Aggregations are slow; the page frame should not wait for them |
| Settings, admin tables | Server-rendered | Small payloads, infrequent visits |
| Export preview | Client-rendered over a fetched draft | It is a canvas, not a document (§20.10) |

No authenticated route is statically cached or served from a shared cache. This is a hard rule from §10.15: every authenticated response is tenant-scoped, and a shared cache is a cross-tenant leak waiting for a cache-key mistake.

### 19.3 Server Components as the default

Components are Server Components unless they need interactivity, browser APIs, or subscription state (§14.4). The performance consequence is direct: code that stays on the server is never shipped, parsed, or executed by the browser.

| Category | Effect on the client bundle |
|---|---|
| Data shaping and formatting for initial render | Not shipped |
| Date and number formatting for static content | Not shipped, and no locale data in the bundle |
| Markdown or rich-text rendering of question bodies | Not shipped — a meaningful saving, since these libraries are large |
| Authorization projection for static affordances | Not shipped; capability data is not duplicated into the client for server-rendered UI |
| Anything under an interaction boundary | Shipped, so boundaries are drawn as deep in the tree as possible |

The discipline that makes this work is placing `'use client'` at the smallest possible node. A single client boundary near the root of a route erases the benefit for that entire subtree, and this is the most common way an App Router codebase quietly becomes a client-rendered application with extra steps.

### 19.4 Streaming and waterfall avoidance

```
  SEQUENTIAL (wrong)                    PARALLEL + STREAMED (right)
  ------------------                    ---------------------------
  shell ──► paper ──► sections ──►      shell ─┬─► paper      ─┐
            questions ──► stats                ├─► sections   ─┼─► stream in
                                               ├─► questions  ─┤   as each
  total = sum of every hop               └─► stats       ─┘   resolves

  total = slowest single hop
```

Requests that do not depend on one another are issued together. Requests that genuinely depend on one another — a paper's sections require the paper's id, which comes from the route, not from a fetch — are not waterfalls at all, because the route parameters are available immediately.

Suspense boundaries are placed at the granularity of independently-useful content: one per dashboard widget, one per secondary detail panel, one around a chart. They are deliberately **not** placed around every component, because each boundary is a skeleton the user watches resolve, and a page that resolves in eleven visible stages feels less finished than one that resolves in three.

Every skeleton reserves the final layout's dimensions. A skeleton that is shorter than its content is a layout shift with extra steps, which is why CLS appears in §19.1 alongside the loading strategy rather than as a separate concern.

### 19.5 Data-fetching performance

| Technique | Application |
|---|---|
| Cache-first reads | A navigation to already-cached data renders immediately and revalidates in the background (§9.5) |
| Prefetch on intent | Hovering or focusing a paper row prefetches its detail; the click then resolves from cache |
| Prefetch the next page | The most likely next action in a paginated list |
| `keepPreviousData` on filter changes | The list never collapses to a skeleton mid-typing (§18.5) |
| Staleness tuned per data class | Reference data measured in hours, job status in real time (§9.5) |
| Deduplication | Concurrent requests for the same key collapse to one (§10.13) |
| Cancellation of superseded requests | Server capacity is not spent on results nobody will see |
| SSE instead of polling | Removes an entire class of periodic requests, which at scale is the single largest reduction in server load (§11) |
| Patch over invalidate where safe | A patch-grade event updates the cache without a refetch (§11.6) |
| No `refetchOnWindowFocus` for expensive queries | Returning to a tab should not fire a dozen analytics aggregations |

The SSE row is worth quantifying. A polled UI checking three resources every five seconds issues about 2,160 requests per user-hour. The same UI on one multiplexed stream issues none, and receives events only when something actually changed. This is a scalability decision expressed as a performance technique.

### 19.6 Bundle strategy

| Concern | Decision | Reasoning |
|---|---|---|
| Route-level splitting | Automatic per route segment | A teacher never downloads the platform-admin surface |
| Role-specific surfaces | Isolated in role route groups (§5) | The super-admin bundle is not part of a teacher's session |
| Heavy libraries | Dynamically imported at point of use | Listed below; these dominate the bundle if imported eagerly |
| Shared chunk | Framework, design system primitives, client, schemas | Cached across routes and across deploys where hashing permits |
| Icons | Per-icon imports only | A whole-set import is the classic silent megabyte |
| Date handling | Small, modern, tree-shakeable; no monolithic date library | Date formatting appears everywhere and must not be expensive |
| Locale data | Loaded on demand, not bundled for every locale | Matters once the product is localised (§28) |
| Polyfills | Modern baseline only | The user base is authenticated professionals on current browsers |
| Duplicate dependencies | Blocked in CI | Two versions of a validation or date library is pure waste |

Libraries that are always dynamically imported:

| Library class | Loaded when |
|---|---|
| Drag-and-drop engine | The section builder is opened (§13.7) |
| Charting | An analytics or dashboard chart enters the viewport |
| Rich-text editor | A question body is opened for editing |
| Math or formula rendering | A question containing notation is first rendered |
| PDF and print preview | The preview pane is opened (§13.13) |
| Spreadsheet parsing (bulk import) | The import dialog is opened (§17.1) |
| Multipart upload machinery | The first large file is selected (§17.16) |
| Diff or version-comparison view | A version comparison is requested |

### 19.7 Rendering and re-render discipline

Interaction latency in a data-dense application is dominated by re-render volume, not by network time.

| Technique | Problem it solves |
|---|---|
| Store access through narrow selectors | A progress tick in the `uploads` store must not re-render the page (§9.12) |
| Coalescing high-frequency events to animation frames | Generation progress and upload progress arrive faster than the display refreshes (§11.16) |
| Uncontrolled form inputs | Keystrokes do not re-render the form tree (§15.1) |
| Memoised list rows with stable keys | Reordering re-renders the moved rows, not all of them |
| Stable identity keys, never array indices | Index keys destroy and rebuild rows on reorder, losing focus and state (§15.10) |
| Virtualisation above roughly 100 rows | Constant DOM size regardless of result-set size (§18.12) |
| Derived values computed in selectors, not stored | Removes an entire class of "two sources disagree" re-render cascades (§9.12) |
| Context split by change frequency | A theme change must not re-render everything subscribed to session context |
| Transitions for non-urgent updates | Typing stays responsive while a large filtered list reconciles |
| Deferred rendering of off-screen panes | The builder's preview pane does not re-render on every keystroke in the editor pane (§13.13) |

### 19.8 Assets, fonts, and images

| Asset | Approach |
|---|---|
| Fonts | Self-hosted, subset, `font-display: swap`, preloaded for the shell weight only |
| Font weights | A deliberately small set; each additional weight is a real download |
| Question images and textbook figures | Served through signed URLs (§17.13), sized and formatted server-side, lazy-loaded below the fold, with explicit dimensions to prevent shift |
| Org branding logo | Sized on upload; the client never downscales a 2 MB original |
| Icons | Inline SVG from a per-icon import; no icon-font, no sprite fetch |
| Charts | Rendered client-side but code-split; no image generation round trip |
| Export artefacts | Never rendered inline for download; the browser navigates to the signed URL |

### 19.9 Performance budgets

Budgets are enforced in CI, because a budget that is only reviewed after a regression is a description of the past.

| Budget | Limit | Enforcement |
|---|---|---|
| Initial JS for an authenticated route (compressed) | ~200 KB | Bundle analysis check on every pull request |
| Shared framework chunk | ~130 KB | Same |
| Any single lazy chunk | ~150 KB | Same; a larger chunk must be split or justified |
| Total CSS | ~50 KB | Tailwind's output is content-derived, so growth signals unused abstractions |
| New dependency over ~30 KB | Requires explicit justification in review | Prevents accretion, which is how bundles actually grow |
| Lighthouse performance score on key routes | Regression threshold rather than an absolute floor | Absolute scores are noisy; regressions are the signal |
| Long tasks over 200 ms on core interactions | Zero tolerance in the builder | Measured in RUM (§26) |

### 19.10 Main-thread protection

A few operations are heavy enough to block interaction and are moved off the main thread or off the client entirely.

| Operation | Where it runs | Reasoning |
|---|---|---|
| Bulk import file parsing | Web worker | Parsing a 20 MB spreadsheet on the main thread freezes the tab for seconds |
| Export rendering | Server-side worker | The authoritative artefact must be identical regardless of the user's device (§12.5) |
| Preview pagination estimate | Main thread, but incremental and cancellable | It is an estimate and explicitly labelled as such (§13.13) |
| Marks and distribution recalculation | Main thread — it is cheap | Contained by the pure-utility rule in §13.6 |
| Search ranking | Server | Never recomputed on the client (§18.8) |
| Large diff computation | Web worker if it exceeds a threshold | Version comparison of a long paper is otherwise a visible stall |

### 19.11 Scalability characteristics

Frontend scalability is about behaviour as data grows, not about server capacity.

| Growth dimension | Behaviour |
|---|---|
| Question bank from 1,000 to 500,000 questions | Constant client cost: cursor pagination, server-side filtering, virtualised rows (§18) |
| Papers per organisation | Constant: paginated lists, no client-side aggregation |
| Sections and questions in one paper | Linear in the visible window; the document model is addressable by node so operations are local (§13.3) |
| Concurrent SSE events during a large generation | Coalesced to frames; monotonic progress means dropped intermediate frames are harmless (§11.15) |
| Organisations per super-admin | Paginated with search; never a full membership list in memory (§8.6) |
| Audit log size | Cursor pagination only; the surface never attempts a total count |
| Notification history | Cursor pagination with an unread count served separately (§23) |
| Analytics data range | Server-side aggregation with long staleness; the client never aggregates raw rows (§12.8) |

The recurring pattern is that no client-side cost is allowed to scale with total tenant data — only with what is on screen. Every violation of that rule is a screen that works in development and fails in the largest real tenant.

### 19.12 Anti-patterns

| Anti-pattern | Why it is rejected |
|---|---|
| A client boundary near the root of a route | Ships the whole subtree to the browser and erases Server Component benefits |
| Fetching in a `useEffect` on mount | Produces waterfalls, no deduplication, no caching, and a guaranteed flash of empty state |
| Client-side filtering or sorting of a paginated set | Not merely slow — it presents one page as the whole result (§18.12) |
| Polling as a substitute for SSE | Thousands of pointless requests per user-hour |
| Storing derived values | Doubles render work and invites disagreement between sources |
| Eagerly loading heavy libraries "because they are used somewhere" | The dominant cause of bundle growth |
| Skeletons that do not match final dimensions | Layout shift dressed as a loading state |
| Unbounded caches or unbounded stores | Memory growth across a long session; the builder's undo stack is bounded for exactly this reason (§13.15) |
| `refetchOnWindowFocus` everywhere | Tab switching triggers a request storm |
| Optimising before measuring | Effort spent on the wrong hop; §26 exists so this is unnecessary |

### 19.13 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Target metrics | Product metrics alongside Core Web Vitals | A page can pass every vital and still feel slow to open | Custom instrumentation required |
| Rendering | Per-route strategy, Server Components by default | Code that stays on the server is never shipped or parsed | Requires disciplined client boundaries |
| Authenticated caching | Never in a shared cache | One cache-key mistake would be a cross-tenant leak | No edge caching for authenticated routes |
| Loading | Streamed with coarse Suspense boundaries | Independently-useful content arrives independently, without eleven visible stages | Boundary placement is a judgement call |
| Data freshness | Per-class staleness plus SSE | Removes polling entirely while keeping views current | An event catalogue to maintain |
| Bundle | Route splitting plus dynamic import of all heavy libraries | Keeps the interactive core small; role surfaces stay separate | Occasional visible load on first use of a feature |
| Interaction cost | Selectors, memoisation, virtualisation, frame coalescing | Re-render volume dominates latency in data-dense UIs | Discipline that must be maintained in review |
| Heavy computation | Web worker or server | Multi-second main-thread work is indistinguishable from a crash | Worker plumbing for a few features |
| Budgets | Enforced in CI | A budget reviewed after a regression only describes the past | Occasional friction on legitimate additions |
| Scalability rule | No client cost may scale with total tenant data | Otherwise the largest real tenant is the one that breaks | Every list needs pagination from day one |

---

## 20. Responsive Design Architecture

### 20.1 Device reality, not device theory

Responsive design here follows observed usage rather than an abstract commitment to identical capability everywhere. Three usage profiles exist, and they want different things.

| Profile | Typical device | What they do | Design consequence |
|---|---|---|---|
| Authoring | Laptop or desktop, 1280–1920 px | Build blueprints, edit sections, reorder questions, approve | Full capability; the builder is designed here first |
| Reviewing | Tablet, 768–1024 px, often landscape | Read a draft, comment mentally, approve or reject | Reading and decision-making optimised; editing available but not primary |
| Monitoring | Phone, 360–430 px | Check generation progress, read notifications, glance at dashboard, approve a short paper | Status, notification, and approval flows must be genuinely good |

The product is a professional authoring tool, so the honest position is **desktop-first for authoring, mobile-excellent for monitoring**. Pretending a 380 px viewport is a good place to drag-and-drop a twelve-section paper produces a bad experience on both ends. What must never happen is a dead end: every surface remains reachable and readable on a phone, even where the primary interaction is replaced by a different one.

### 20.2 Breakpoints

Tailwind's default scale is used unchanged rather than replaced with product-specific names, because a custom scale forces every developer to learn a private vocabulary for no benefit.

| Token | Min width | Layout meaning |
|---|---|---|
| (base) | 0 | Single column, drawer navigation, bottom action bar |
| `sm` | 640 | Larger phones and small tablets in portrait; two-column forms begin |
| `md` | 768 | Tablet; icon rail navigation, two-pane layouts appear |
| `lg` | 1024 | Small laptop; persistent sidebar, builder becomes usable |
| `xl` | 1280 | Standard laptop; the builder's three-pane layout appears |
| `2xl` | 1536 | Large desktop; content max-width caps, wider preview pane |

Two rules govern their use. Styles are written **mobile-first**, with breakpoints adding capability rather than removing it, because subtractive responsive CSS is where specificity bugs breed. And layout decisions are made at the **container** level where a component can appear in panes of different widths — a question card in a full-width list and the same card in a 380 px builder pane are the same component in different containers, not two components.

### 20.3 The responsive shell

The shell's three configurations are specified in §8.10; the reasoning is here.

```
  ≥ lg (1024)              md (768–1023)            < md (768)
  ┌────┬─────────────┐     ┌──┬────────────────┐    ┌───────────────┐
  │ S  │  header     │     │▐ │  header        │    │ ☰  header   ● │
  │ i  ├─────────────┤     │  ├────────────────┤    ├───────────────┤
  │ d  │             │     │  │                │    │               │
  │ e  │   content   │     │  │    content     │    │   content     │
  │ b  │             │     │  │                │    │               │
  │ a  │             │     │  │                │    │               │
  │ r  │             │     │  │                │    ├───────────────┤
  └────┴─────────────┘     └──┴────────────────┘    │ ⌂  ✎  ⌸  ⚙   │
   persistent, collapsible  icon rail, expands       drawer + bottom
                            on hover/focus           tab bar
```

| Decision | Reasoning |
|---|---|
| Persistent sidebar at `lg` and above | Navigation is used constantly in authoring sessions; hiding it costs a click every time |
| Icon rail at `md` | Preserves navigational awareness while returning ~200 px to content, which is what a tablet needs most |
| Drawer at base | A 360 px viewport cannot spare horizontal space for navigation at all |
| Bottom tab bar on phones | Thumb-reachable access to the three or four destinations that matter on a phone, per role |
| No layout change on orientation alone | Orientation is inferred from the resulting width; a separate orientation rule produces surprising jumps |
| Shell chrome never scrolls with content | Scroll containment is a shell property (§8.11), unchanged across breakpoints |

### 20.4 Navigation adaptation by role

The bottom bar cannot hold twelve destinations, so it holds the role's most frequent ones and defers the rest to the drawer.

| Role | Bottom bar destinations | In the drawer |
|---|---|---|
| `teacher` | Dashboard, Papers, Question Bank, More | Textbooks, Templates, Notifications, Settings, Profile |
| `coaching_admin` | Dashboard, Papers, Approvals, More | Question Bank, Textbooks, Users, Analytics, Billing, Settings |
| `super_admin` | Organisations, Platform, Notifications, More | Prompt versions, Audit log, Global analytics, Settings |

Destinations are still derived from the same capability projection as the sidebar (§7), so an unavailable destination is absent rather than present-and-failing. The drawer and the bottom bar are two projections of one navigation model, not two hand-maintained lists.

### 20.5 Data-dense surfaces: tables to cards

Tables are the hardest responsive problem in the product, and horizontal scrolling a ten-column table on a phone is not a solution.

| Viewport | Presentation | Reasoning |
|---|---|---|
| `≥ lg` | Full table, all columns, inline row actions | Comparison across rows is the point of a table |
| `md` | Table with secondary columns dropped; a detail panel carries the rest | Preserves scanning while fitting the width |
| `< md` | Stacked cards: primary identifier, two or three key facts, one primary action, overflow menu | A card is legible at 360 px; a table row is not |

The card projection is defined per surface, declaring which fields are primary, which are secondary, and which are available only in detail. That declaration lives with the surface, so the table and card views cannot drift apart. Bulk selection survives the transformation — a card gets a checkbox — because the filter-descriptor selection model (§18.10) does not depend on a table shape.

Sorting and filtering move from column headers into an explicit control on small screens: a sort sheet and a filter sheet, both full-height, both applying on confirmation rather than per tap, because a filter that refetches on every tap in a modal sheet is expensive and jarring.

### 20.6 The paper builder on small screens

The builder is the surface where the desktop-first position has real consequences, so its degradation is specified rather than emergent.

```
  ≥ xl : three panes side by side          (§13.2)
  lg   : two panes — outline + editor; preview as an overlay
  md   : single pane with a segmented switch: Outline | Editor | Preview
  < md : single pane, sequential flow, editing restricted
```

| Capability | `< md` behaviour | Reasoning |
|---|---|---|
| Read the draft | Full | The most common phone need |
| Edit question text and marks | Available | Small, targeted edits are reasonable |
| Reorder by drag | Replaced by explicit "Move up / Move down / Move to section" controls | Dragging within a scrolling single-column viewport is unreliable; the keyboard-reorder path (§13.7) already provides the semantics |
| Lock, unlock, regenerate one question | Available | Single-target actions fit a small screen |
| Blueprint editing | Read-only with a clear explanation and a "continue on a larger screen" note | A blueprint form is genuinely a wide-viewport task, and a cramped version invites mistakes in the most consequential input |
| Approve or reject | Available | The reviewing profile matters and often happens on a phone |
| Export | Available | It is one action producing a server-side artefact |
| Print preview | Available but explicitly labelled as an estimate | Page-break estimation at 360 px is not representative (§13.13) |

The read-only blueprint is a deliberate restriction rather than a missing feature, and the UI says which it is. Silently offering a degraded blueprint form would produce papers whose structure the teacher did not intend.

### 20.7 Touch adaptation

| Concern | Decision |
|---|---|
| Target size | Minimum 44 × 44 px for any interactive element on touch, including icon-only controls |
| Spacing | Increased vertical rhythm in lists on touch, so adjacent row actions are not mis-tapped |
| Hover-only affordances | Never the sole path to an action; every hover-revealed control has a persistent equivalent on touch |
| Tooltips | Supplementary only; the information also exists in visible text or an accessible label |
| Long-press | Not used for primary actions; discoverability is too low |
| Swipe | Optional accelerator on notification and list rows, always with a visible equivalent control |
| Drag handles | Sized for touch where dragging is retained; replaced entirely where it is not (§20.6) |
| Input types | Numeric keypad for marks and counts, email keyboard for email, so the correct keyboard appears |
| Sticky footers vs the keyboard | Primary actions move above the on-screen keyboard rather than being covered by it |
| Pointer detection | Capability-based (`hover`, `pointer: coarse`) rather than user-agent sniffing | 

The hover row is the most frequently violated rule in data-dense UIs: row actions that appear on hover are invisible and unreachable on a tablet, which is exactly the reviewing profile's device.

### 20.8 Forms and dialogs

| Element | `≥ md` | `< md` | Reasoning |
|---|---|---|---|
| Form layout | Two columns where fields are naturally paired | Single column, always | Two-column forms at 360 px produce unreadable label wrapping |
| Field width | Sized to expected content | Full width | Consistency beats optimisation at narrow widths |
| Dialogs | Centred modal, content-sized | Full-screen sheet | A small modal on a phone leaves the user peering through a keyhole |
| Multi-step wizard | Horizontal stepper | Compact "Step 2 of 4" with the step name | The full stepper does not fit and is not needed to orient |
| Destructive confirmations | Modal with the action on the right | Sheet with the primary action full-width and the destructive action visually distinct | Prevents accidental confirmation by thumb |
| Inline validation | Beside or below the field | Below the field, always | Space for the message must be reserved either way (§15.7) |
| Autosave indicator | In the header | In the sticky footer near the primary action | Where the user's attention already is |

### 20.9 Real-time and progress on small screens

Progress reporting is one of the phone's best use cases, so it is treated as a first-class small-screen surface rather than a shrunken desktop panel: a full-width progress card with the stage name, the question counter, and the cancel control, plus a compact persistent indicator in the header when the user navigates elsewhere. The upload tray (§17.8) collapses to a single summary row that expands into a sheet. SSE behaviour itself is unchanged by viewport, with one exception noted in §11.17 — a backgrounded tab on a mobile browser is far more likely to have its connection suspended, so reconnection with reconciliation on return to foreground is the normal path on mobile rather than an edge case.

### 20.10 The print and export preview exception

The export preview is the one surface that is deliberately **not** responsive. It renders a fixed page geometry — A4 or Letter — and scales that geometry to fit the viewport.

| Property | Behaviour |
|---|---|
| Layout | Fixed page dimensions, never reflowed to the viewport |
| Small screens | Zoomed to fit width, pannable, with a zoom control |
| Why | The user is inspecting what will be printed; reflowing it would make the preview a lie |
| Relationship to the artefact | Advisory. The authoritative artefact is produced server-side (§12.5), so the preview is a visual approximation and is labelled as one |
| Scroll containment | Exempt from the shell's containment rule (§8.11), since the page canvas is the scroll surface |

This is a case where consistency with the rest of the responsive system would actively harm the user, and the exception is documented so it is not "fixed" later.

### 20.11 Implementation approach

| Concern | Decision | Reasoning |
|---|---|---|
| Breakpoint source | Tailwind utility variants | One source of truth in the design system; no parallel JS breakpoint constants to drift |
| JS viewport queries | Only where layout cannot express the behaviour — for example, choosing a sheet over a modal | Media-query-driven JS re-renders on resize and is a hydration-mismatch risk |
| Container queries | Used for components appearing in panes of varying width | The builder's panes make viewport width the wrong input (§20.2) |
| Server-side rendering | No device detection; the server renders one markup tree that adapts in CSS | Device sniffing is unreliable and fragments the cache |
| Avoiding hydration mismatch | No render decision depends on a value unavailable on the server | A CSS-first approach makes this structural rather than careful |
| Testing | Automated at three widths (360, 768, 1440) plus a touch-emulation pass | Catches the hover-only and target-size regressions that reviews miss |
| Zoom | Layout survives 200% browser zoom, which is also an accessibility requirement (§21) | Zoom and narrow viewports exercise the same code paths |

### 20.12 Failure handling

| Situation | Behaviour |
|---|---|
| A capability is unavailable at this width | Stated explicitly, with the reason and the suggestion to use a wider screen — never a silently missing control |
| Content exceeds the viewport unexpectedly | The container scrolls; the shell does not, so navigation stays reachable |
| A long unbroken string (a URL in a question) | Wrapped or truncated with the full value available, never widening the layout |
| A wide table inside a narrow container | Card projection at `< md`; horizontal scroll within the container elsewhere, never on the page |
| An image wider than its container | Constrained to the container with the intrinsic ratio preserved |
| On-screen keyboard covering the primary action | The action is repositioned above the keyboard |
| Orientation change mid-interaction | Scroll position and form state are preserved; the layout re-flows without remounting |

### 20.13 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Overall posture | Desktop-first for authoring, mobile-excellent for monitoring | Matches how the product is actually used; feature parity at 360 px would be worse for everyone | Some authoring capability is restricted on phones |
| Breakpoints | Tailwind defaults, unrenamed | No private vocabulary to learn; one source of truth | Not semantically named for this product |
| CSS direction | Mobile-first, additive | Subtractive responsive CSS is where specificity bugs breed | Requires discipline in review |
| Layout input | Container queries for pane-hosted components | The builder makes viewport width the wrong input | A newer capability to rely on |
| Navigation | Sidebar → icon rail → drawer plus bottom bar | Preserves awareness at each width the device can afford | Three configurations to maintain |
| Tables | Card projection below `md`, declared per surface | Horizontal scrolling a ten-column table is not a solution | A second presentation per table |
| Builder on phones | Read, targeted edits, approve; explicit reorder controls; read-only blueprint | Dragging and blueprint authoring at 360 px invite real mistakes | A stated capability gap |
| Touch | 44 px targets; no hover-only affordances | The reviewing profile is on a tablet, where hover does not exist | Slightly larger controls on desktop lists |
| Device detection | None; CSS adaptation only | Sniffing is unreliable and fragments caching | Cannot pre-optimise markup per device |
| Print preview | Fixed page geometry, scaled | Reflowing a print preview makes it a lie | An intentional exception to the whole system |
| Restrictions | Always explained, never silent | A missing control reads as a bug | Extra explanatory copy |

---

## 21. Accessibility Architecture

### 21.1 Target and rationale

The product targets **WCAG 2.1 Level AA**. This is not an aspiration attached to the end of the project; it is a constraint that shapes the component tier (§14), the form layer (§15), the drag-and-drop model (§13.7), and the real-time announcements (§11).

| Reason | Detail |
|---|---|
| Users | Educational institutions include staff with visual, motor, and cognitive disabilities; a teacher cannot opt out of the tool their employer bought |
| Procurement | Public and many private education buyers require an accessibility conformance statement; an inaccessible product is disqualified before evaluation |
| Regulation | Accessibility obligations apply to educational institutions in most of the markets this product targets, and flow through to the software they use |
| Quality correlation | Keyboard reachability, focus management, and semantic structure are the same properties that make a product testable and fast |

AA rather than AAA is a considered choice: AAA's contrast and language-level requirements would constrain the visual system and the domain vocabulary in ways that do not serve this audience, while AA is achievable across every surface without exception. Where a surface exceeds AA cheaply — for example, contrast on primary text — it does.

### 21.2 Where accessibility is enforced

Accessibility fails when it is one team member's review responsibility. It holds when it is a property of the primitives everyone already uses.

```
  ┌──────────────────────────────────────────────────────────┐
  │ Tier 1  Primitives (shadcn/ui over Radix)                │
  │   focus trapping, roles, keyboard interaction,           │
  │   labelling contracts — correct by construction          │
  └───────────────────────┬──────────────────────────────────┘
                          │ inherited
  ┌───────────────────────▼──────────────────────────────────┐
  │ Tier 2  Shared components (§14.9)                        │
  │   reserve message space, associate labels and errors,    │
  │   own their own live regions                             │
  └───────────────────────┬──────────────────────────────────┘
                          │ composed
  ┌───────────────────────▼──────────────────────────────────┐
  │ Tier 3/4  Feature and route components                   │
  │   heading order, landmarks, focus on navigation,         │
  │   meaningful names for domain actions                    │
  └───────────────────────┬──────────────────────────────────┘
                          │ verified
  ┌───────────────────────▼──────────────────────────────────┐
  │ CI  automated axe pass + keyboard-path tests (§27)       │
  │ Manual  screen-reader and keyboard-only review           │
  └──────────────────────────────────────────────────────────┘
```

Choosing Radix-based primitives (§14.3) was in significant part an accessibility decision. Dialog focus trapping, menu roving tabindex, combobox `aria-activedescendant`, and popover dismissal are the behaviours teams most reliably get wrong, and they are solved once in the primitives rather than repeatedly in features. Owning the source means the project can fix a gap without waiting for a library release — and, equally, must not casually strip the attributes that make a primitive correct.

### 21.3 Semantic structure

| Concern | Rule |
|---|---|
| Landmarks | One `banner`, one `navigation`, one `main`, one `contentinfo` per page; the builder's panes are labelled regions |
| Heading order | Exactly one `h1` per route, no skipped levels; headings describe content, not styling |
| Page title | Updated on navigation to "`<Resource>` · `<Section>` · Product" so tab lists and history are usable |
| Lists | Real list semantics for lists; tables only for tabular data, never for layout |
| Tables | `caption` or an accessible name, `th` with `scope`, sortable headers exposing `aria-sort` |
| Buttons vs links | Actions are buttons, navigation is links — never a `div` with a click handler |
| Icon-only controls | Always have an accessible name; the icon is decorative and hidden from assistive technology |
| Decorative graphics | Hidden; informative graphics have text alternatives |
| Charts | Always accompanied by the underlying data table (§12.8), which is the text alternative |
| Reading order | DOM order matches visual order; no layout technique reorders content away from its sequence |

The chart rule is worth calling out because it is often treated as an accessibility concession. It is not: the data table beneath every chart is the surface admins use to read exact numbers, and it satisfies the text-alternative requirement as a by-product of being genuinely useful.

### 21.4 Keyboard operability

Every action in the product is reachable and completable by keyboard alone. Where an interaction is inherently pointer-oriented, an equivalent keyboard path exists and is documented, not merely technically present.

| Interaction | Keyboard path |
|---|---|
| Navigation | Skip-to-content link first in the tab order; sidebar is a navigable list; the drawer traps focus while open |
| Command palette | A global shortcut opens it; it is the fastest path to any route or action for keyboard users |
| Dialogs and sheets | Focus moves in on open, is trapped, returns to the invoking control on close; Escape dismisses unless there are unsaved changes |
| Menus | Arrow-key roving focus, Home/End, type-ahead, Escape to close |
| Comboboxes and search | Arrow keys to move through options, Enter to select, Escape to revert; the active option is announced |
| Tables | Header activation to sort; row actions reachable in the tab order without hover |
| Question reorder | Explicit grab/move/drop keyboard mode with announcements — mandatory, not optional (§13.7) |
| Multi-select in lists | Space toggles; Shift-arrow extends; the selection count is announced |
| Builder pane switching | A documented shortcut cycles the outline, editor, and preview regions |
| Autosave and submit | The primary action is always reachable without traversing the entire form |
| Tab traps | None outside intentional modal traps; verified in CI |

Focus visibility is non-negotiable: a visible focus indicator with sufficient contrast on every focusable element, never removed, and never replaced by a hover style. The indicator is a design-system token so it cannot be locally overridden into invisibility.

### 21.5 Focus management on navigation and async change

| Event | Focus behaviour |
|---|---|
| Route change | Focus moves to the main content region's heading; the route name is announced |
| Streamed content arriving | Focus is never moved by late-arriving content — a stolen focus mid-typing is the worst offender in streamed UIs |
| Dialog open and close | In on open, restored to the invoker on close |
| Row deleted from a list | Focus moves to the next row, or to the list heading if the list is now empty — never lost to `body` |
| Validation failure on submit | Focus moves to the first invalid field, which is also announced (§15.7) |
| Page change in a paginated list | Focus moves to the top of the result region (§18.11) |
| Generation completes | Focus is not moved; the completion is announced and the primary next action is reachable |
| Toast appears | Focus is not moved; the toast is announced politely and its actions are reachable via a documented shortcut |
| Org switch | The hard reset (§9.7) lands focus on the new context's main heading, with the active organisation announced |

The rule underneath all of these: **the system moves focus in response to the user's action, never in response to the server's.** Asynchronous events announce; they do not seize.

### 21.6 Live regions and announcements

Real-time updates (§11) are the accessibility risk unique to this product. A generation run emitting fifty progress events would, if naively wired to a live region, produce fifty interruptions.

| Update | Politeness | Frequency control |
|---|---|---|
| Generation progress | Polite | Announced at meaningful milestones only — start, 25/50/75%, completion — not per event |
| Question ready during generation | Polite | Coalesced: "12 of 20 questions ready", not one announcement per question |
| Generation complete or failed | Polite, and a focusable summary | Once |
| Upload transfer progress | Not announced continuously | Phase transitions only (§17.6) |
| Ingestion stage change | Polite | Per stage, since stages are minutes apart |
| Search result count | Polite | Debounced to the settled query (§18.11) |
| Autosave status | Polite, low priority | Only on transition to saved or failed, never per attempt |
| Validation errors on submit | Assertive | Once, summarising the count, with focus moved to the first error |
| Session expiry warning | Assertive | Once |
| Destructive-action result | Polite | Once |
| Toast notifications | Polite | Once per toast; the queue is bounded (§23) |

Assertive is reserved for the three cases where the user must not continue unaware: a submit-blocking validation failure, imminent session expiry, and a failure that lost data. Everything else is polite. Live regions are created with the surface, never injected at announcement time, since a region added and populated in the same frame is unreliably announced.

### 21.7 Forms

| Requirement | Implementation |
|---|---|
| Every field has a visible label | Placeholders are never the label; a floating label is still a real label |
| Programmatic association | Label, description, and error are associated with the control, so a screen reader reads the full context (§15.7) |
| Error identification | `aria-invalid` plus an associated message that names the problem and how to fix it |
| Error summary | Multi-error submits produce a focusable summary listing each error as a link to its field |
| Required fields | Marked visually and programmatically; "required" is never conveyed by colour or an asterisk alone |
| Grouping | Related controls in a fieldset with a legend — essential for the blueprint's distribution groups (§13.4) |
| Autocomplete | Correct `autocomplete` tokens on identity and contact fields |
| Reserved message space | Space for the error is reserved, so validation does not shift the layout (§15.7) |
| Timeouts | The session expiry warning is announced with an option to extend, never a silent logout mid-form (§21.9) |
| Instructions | Format requirements stated before the input, not only after a failure |

### 21.8 Colour, contrast, and visual design

| Requirement | Rule |
|---|---|
| Text contrast | 4.5:1 for body text, 3:1 for large text, verified for both themes (§22) |
| Non-text contrast | 3:1 for control boundaries, focus indicators, and meaningful graphics |
| Colour independence | Status is never colour-only: approval states, difficulty levels, and validation severity each carry an icon or text label |
| Difficulty and Bloom bars | Labelled with values, not read from bar colour alone (§13.10) |
| Chart series | Distinguished by pattern or direct labelling as well as hue |
| Dark theme | Contrast verified independently; it is not assumed to inherit compliance from the light theme |
| High contrast / forced colours | Respected; the interface remains usable when the platform overrides colours |
| Reduced motion | Honoured: progress animations become discrete updates, drag animations are removed, transitions collapse |
| Reduced transparency | Respected where the platform reports it |
| Text resizing | Layout survives 200% zoom and text-only scaling with no loss of content or function (§20.11) |
| Line length and spacing | Readable measure on question text; user-adjusted spacing does not break layout |

### 21.9 Cognitive and time-related considerations

This product asks users to make many small decisions under time pressure at the end of a term, which makes cognitive load a first-class accessibility concern rather than a usability nicety.

| Concern | Response |
|---|---|
| Session timeout | Silent refresh keeps a working session alive (§6); a warning precedes any forced expiry, with an option to continue |
| Data loss on expiry | Builder work is mirrored to `sessionStorage` and restored after re-authentication (§13.16) |
| Complex forms | Progressive disclosure in the blueprint; a wizard where steps are genuinely sequential (§15.11) |
| Error recovery | Messages name the cause and the next action; nothing is destroyed by a failed submit |
| Destructive actions | Confirmations restate what will happen in domain terms, not "Are you sure?" |
| Undo | Available for reversible builder operations (§13.15), which reduces the cost of a mistake |
| Consistency | Identical patterns for identical operations across every module, so one learned behaviour transfers |
| Progress honesty | Stage names and counters instead of an indeterminate spinner during multi-minute work (§17.6) |
| Language | Plain domain language; no unexplained jargon in error copy (§16.13) |
| No unexpected context change | Focus and content do not change without user action (§21.5) |

### 21.10 Assistive-technology support matrix

| Environment | Support level |
|---|---|
| Keyboard only, any platform | Full — the baseline every other environment builds on |
| Screen reader on Windows (NVDA, JAWS) with Chrome or Edge | Full; the primary manual test configuration |
| VoiceOver on macOS with Safari | Full |
| VoiceOver on iOS | Full for monitoring, reviewing, and approval flows; authoring is limited by the responsive restrictions in §20.6 |
| TalkBack on Android | Same as iOS |
| Browser zoom to 200% | Full |
| OS-level text scaling | Full |
| Voice control and switch access | Supported through standard semantics and target sizing; not separately certified |
| Forced-colours mode | Full |

### 21.11 Verification

| Layer | Method | Catches |
|---|---|---|
| Primitives | Unit tests asserting roles, names, and keyboard behaviour | Regressions from local overrides that strip attributes |
| Components | Automated axe checks in component tests | Missing names, contrast failures, invalid nesting |
| Routes | Automated axe in end-to-end tests at three viewports | Landmark and heading-order problems that only appear in composition |
| Critical journeys | Keyboard-only end-to-end tests: create paper → generate → reorder → approve → export | The paths that must never break, verified without a pointer |
| Manual | Screen-reader review of the builder, the generation flow, and every form, each release | Announcement quality, which no automated tool can assess |
| Regression | Any accessibility bug ships with a test | Prevents the same defect twice |

Automated tooling is treated as a floor, not a ceiling. Axe cannot tell whether an announcement is *useful*, whether focus lands somewhere *sensible*, or whether an error message explains *what to do*. Those are the questions the manual pass answers, and they are the ones users actually notice.

### 21.12 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Target | WCAG 2.1 AA across every surface | Achievable everywhere; required by procurement and regulation | AAA contrast and language levels not pursued |
| Enforcement point | Accessible primitives, inherited upward | Accessibility as a property of the tools, not a review checklist | Overriding a primitive's attributes must be treated as a defect |
| Primitive choice | Radix-based, source owned | The hardest behaviours are solved once; gaps can be fixed without a release | Responsibility for keeping them correct |
| Keyboard parity | Every action completable without a pointer | Legal requirement and a quality signal | Drag-and-drop needs a full parallel implementation |
| Reorder | Keyboard mode with announcements is mandatory | Drag-only reordering excludes keyboard and screen-reader users entirely | Significant implementation cost in §13.7 |
| Focus policy | The user moves focus; the server announces | Prevents stolen focus in a heavily streamed, event-driven UI | Requires explicit focus decisions per async event |
| Live regions | Polite by default; assertive for three named cases | Fifty progress events must not become fifty interruptions | Milestone logic per event type |
| Status encoding | Never colour alone | Colour-blind users and forced-colours mode | Every status needs an icon or label |
| Dark theme | Independently verified | Compliance does not transfer between themes | Two contrast audits |
| Motion | `prefers-reduced-motion` honoured throughout | Vestibular safety; also improves perceived performance | Two variants of progress and drag feedback |
| Verification | Automated floor plus manual screen-reader passes | Tooling cannot judge announcement quality | Recurring manual effort each release |

---

## 22. Theme System

### 22.1 What the theme system is responsible for

The theme system answers three separate questions that are often conflated: what colour is this, what is this element's role in the interface, and which visual mode is active. Keeping them separate is what allows a dark mode, a per-organisation brand, and a print stylesheet to coexist without three parallel implementations.

| Responsibility | Scope |
|---|---|
| Design tokens | Colour, spacing, radius, typography, elevation, motion — the vocabulary every component uses |
| Mode | Light, dark, or follow the operating system |
| Density | Comfortable or compact, for data-dense admin surfaces |
| Organisation branding | A logo and an accent colour applied to exports and, in a constrained way, to the interface |
| Print and export appearance | A distinct token set with entirely different constraints (§22.9) |
| Accessibility guarantees | Contrast compliance in every mode (§21.8) |

Out of scope, deliberately: user-authored themes, arbitrary colour pickers for interface chrome, and per-user layout customisation. Each would multiply the contrast-verification surface without serving a real need in a professional authoring tool.

### 22.2 Token architecture

Tokens are layered, and components are only permitted to consume the outermost layer.

```
  ┌─────────────────────────────────────────────────────────────┐
  │ Layer 1  Primitives — raw values                            │
  │   blue-500, slate-900, space-4, radius-md, duration-fast    │
  │   No component may reference these directly.                │
  └──────────────────────────┬──────────────────────────────────┘
                             │ mapped per mode
  ┌──────────────────────────▼──────────────────────────────────┐
  │ Layer 2  Semantic — role in the interface                   │
  │   background, foreground, muted, border, input, ring,       │
  │   primary, secondary, destructive, warning, success         │
  │   Each has a paired -foreground for guaranteed contrast.    │
  └──────────────────────────┬──────────────────────────────────┘
                             │ specialised where a domain needs it
  ┌──────────────────────────▼──────────────────────────────────┐
  │ Layer 3  Domain — product meaning                           │
  │   difficulty-easy/medium/hard, bloom-remember…create,       │
  │   status-draft/review/approved/rejected,                    │
  │   source-textbook/ai-generated/manual,                      │
  │   severity-error/warning/info                               │
  └─────────────────────────────────────────────────────────────┘
```

The paired-foreground rule in layer 2 is the mechanism that makes contrast a property of the token system rather than of each usage: choosing `primary` obliges the paired `primary-foreground`, and that pair is contrast-verified once per mode. A developer cannot accidentally place low-contrast text on a themed surface without leaving the token vocabulary entirely, which review catches.

Layer 3 exists because domain colours have meanings that must stay stable across the product. "Hard" is one colour in the question bank, the difficulty bars (§13.10), the analytics charts, and the export preview. Defining it once prevents the drift where three surfaces disagree about which red means hard.

### 22.3 Implementation mechanism

| Concern | Decision | Reasoning |
|---|---|---|
| Token carrier | CSS custom properties on the root element | Mode switching becomes an attribute change with no re-render and no JS in the paint path |
| Tailwind integration | Utilities reference the semantic tokens, not raw palette values | `bg-primary` works in both modes; `bg-blue-500` does not |
| Mode selector | A class or `data-theme` attribute on the document element | Cascades to everything, including portalled dialogs and toasts |
| Scoped overrides | The same custom properties re-declared on a subtree | Lets the print preview and the export canvas use a different token set in place (§22.9) |
| No JS theme objects | Tokens are not duplicated into a JavaScript object consumed at render time | A JS theme object means every themed component re-renders on switch, and the two definitions drift |
| Charts | Read the computed custom property values | Keeps chart colours consistent with the interface in every mode without duplicating the palette |
| Motion tokens | Durations and easings are tokens, with a reduced-motion variant | Makes `prefers-reduced-motion` a token concern rather than per-component logic (§21.8) |

### 22.4 Mode resolution and the flash problem

The user's stored preference is one of three values: `light`, `dark`, or `system`. `system` is the default, and it resolves against the OS preference at render time.

```
  request
     │
     ├─► inline script (before first paint)
     │      reads localStorage: 'light' | 'dark' | 'system'
     │      if system → reads prefers-color-scheme
     │      sets data-theme on <html>
     │
     ├─► first paint: already correct — no flash
     │
     └─► hydration: provider reads the same attribute as its
            initial state, so client and server agree

  later: OS preference changes
     └─► media-query listener updates the attribute,
            but only while the stored preference is 'system'
```

The pre-paint inline script is the only script in the application permitted to run before hydration, and it exists for one reason: any theme resolution that happens after first paint produces a visible flash of the wrong mode. Rendering a default mode server-side and correcting it on hydration is the common shortcut, and it produces a white flash for every dark-mode user on every cold navigation. The trade-off is a small blocking script and the requirement that the provider derive its initial state from the DOM attribute rather than from storage, so hydration cannot disagree with the painted result.

Mode preference is stored in `localStorage` rather than server-side (§9.9) because it is device-specific — the same teacher may want dark on a laptop at night and light on a shared classroom desktop — and because a server round trip cannot participate in a pre-paint decision.

### 22.5 Density

| Mode | Applied to | Effect |
|---|---|---|
| Comfortable (default) | All surfaces | Standard spacing and control heights, touch-friendly |
| Compact | Admin tables, the question bank list, the audit log | Reduced row height and vertical rhythm; roughly 40% more rows per screen |

Density is implemented as spacing-token overrides on a subtree, not as per-component props, so a table does not need a `dense` variant of every cell, badge, and button it contains. It is a user preference persisted alongside the mode, and it never applies on touch pointers, where reducing target size below 44 px would violate §20.7. That interaction — a preference silently ignored on touch — is stated in the interface rather than left as a mystery.

### 22.6 Organisation branding

Branding is where multi-tenancy meets theming, and the constraint is sharper than it first appears.

| Element | Where branding applies | Where it does not |
|---|---|---|
| Organisation logo | Export headers, print preview, the org switcher, login for an org-specific entry point | Not substituted for product chrome |
| Accent colour | Export accents, and a bounded set of interface accents | Not applied to semantic status colours, validation severity, or the difficulty and Bloom scales |
| Typography | Export typography may be selected from a curated list | Interface typography is fixed |

Two rules make this safe. **Branding may never alter a colour that carries meaning** — an organisation whose brand colour is red must not turn "approved" into red, and must not make a warning indistinguishable from a success. And **any brand-derived colour is contrast-checked at the point of use**, with an automatic fallback to the semantic token when the supplied colour fails against its surface. The organisation is told at upload time that their colour did not pass, rather than discovering it as an unreadable button later.

Brand values arrive with organisation data through the normal query cache and are applied by setting a small number of scoped custom properties. They are therefore subject to the org-switch hard reset (§9.7): switching organisations replaces the branding along with everything else, and a stale brand colour cannot survive into another tenant's view.

### 22.7 What theming must not do

| Prohibited | Reasoning |
|---|---|
| Convey status by hue alone in any mode | Fails §21.8 for colour-blind users and forced-colours mode |
| Allow arbitrary user-chosen interface colours | Every combination would need contrast verification; the surface is unbounded |
| Change layout, spacing scale, or component structure by theme | A theme that changes layout is a second design system in disguise |
| Vary domain colour meanings between surfaces | Layer 3 exists to prevent exactly this |
| Encode theme decisions in component logic | A component that branches on `isDark` is a token that was never defined |
| Ship mode-specific images without a mode-neutral fallback | Doubles the asset surface and breaks in forced colours |
| Apply branding to the super-admin platform surface | Platform context must be visually unambiguous when operating inside a tenant (§8.6) |

### 22.8 Dark mode is a first-class mode

Dark mode is not an inverted light mode. Three properties are handled explicitly:

Elevation cannot be expressed by shadow in a dark interface, because shadows are nearly invisible against dark surfaces. Dark mode conveys elevation through surface lightness steps instead, which is why elevation is a semantic token (`surface`, `surface-raised`, `surface-overlay`) rather than a shadow utility applied ad hoc.

Saturated colours vibrate against dark backgrounds and read as glowing. The dark palette uses desaturated, lightness-adjusted variants of each accent, mapped through layer 2, so components are unaware of the adjustment.

Images and figures from textbooks are content, not chrome. They are rendered on a light plate in dark mode rather than inverted, because inverting a scanned diagram makes it wrong. This is the same reasoning as the print-preview exception in §20.10: fidelity to the source beats consistency with the theme.

### 22.9 Print and export appearance

The print token set is a separate mode, not a variant of the light theme.

| Constraint | Rule |
|---|---|
| Mode | Always light tokens, regardless of the user's preference — nobody prints a dark paper |
| Colour | Assumed monochrome; every distinction that matters must survive without colour |
| Interactive chrome | Absent — no navigation, buttons, or hover affordances in the printed output |
| Page geometry | Fixed A4 or Letter with defined margins (§20.10) |
| Typography | Print-appropriate sizes and line heights, independent of interface typography |
| Branding | Applied — the logo and accent are most visible here, and this is the primary reason branding exists |
| Authority | The server-rendered artefact is authoritative; the browser print path is a convenience (§12.5) |

Stating that the printed artefact is monochrome-safe is not a limitation of the theme system; it is a property of the domain. Question papers are photocopied.

### 22.10 Failure handling

| Failure | Behaviour |
|---|---|
| `localStorage` unavailable or blocked | Fall back to `system`; the switcher still works for the session |
| Stored preference is an unrecognised value | Treated as `system`; the invalid value is overwritten |
| Branding fetch fails | Product default tokens are used; no unstyled or half-branded state |
| Brand colour fails contrast | Semantic token is substituted and the organisation is notified at upload time |
| Logo asset fails to load | Organisation name renders as text in the same slot |
| Forced-colours mode active | Token colours yield to the platform; layout and semantics remain intact |
| OS preference changes mid-session | Applied immediately when the preference is `system`, ignored otherwise |
| Theme changes while a print preview is open | The preview is unaffected — it uses the scoped print tokens |

### 22.11 Extensibility

| Future capability | How the architecture accommodates it |
|---|---|
| Additional modes (high contrast, sepia for long reading) | A new layer 2 mapping and a fourth stored value; no component changes |
| Per-organisation interface themes | Layer 2 becomes org-scoped rather than global, with the same contrast gate at application time |
| Per-program export templates | Print token overrides scoped to a template, which is already how the print set is applied |
| Localisation-driven typography | Font tokens become locale-aware at layer 1 |
| White-label deployments | Product chrome tokens become configurable at build time, using the mechanism §22.6 already establishes |

### 22.12 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Token layering | Primitive → semantic → domain, with components restricted to the outer layers | Mode switching and branding become remapping problems, not component problems | A vocabulary that must be learned and enforced |
| Contrast | Paired `-foreground` tokens, verified per mode | Makes accessible contrast the default rather than a review outcome | Every surface colour needs a paired foreground |
| Carrier | CSS custom properties | Mode switching without re-rendering the tree; portalled content inherits automatically | Chart libraries must read computed values |
| No JS theme object | Tokens exist only in CSS | Avoids a duplicate definition that drifts and forces re-renders | Slightly less ergonomic for imperative canvas work |
| Flash prevention | Pre-paint inline script; provider derives state from the DOM | A flash of the wrong mode on every cold load is unacceptable | One small blocking script, and a hydration rule to respect |
| Preference storage | Device-local for mode and density | Device-specific by nature, and required before any server round trip | Not portable across devices |
| Density | Spacing-token override on a subtree; disabled on touch | Avoids a dense variant of every component; preserves touch target sizes | A preference that is conditionally ignored, so it must be explained |
| Branding | Bounded: exports fully, interface accents only | Tenants get identity without breaking meaning or contrast | Less brand expression than tenants may request |
| Branding safety | Contrast-gated with automatic fallback and upload-time notice | An unreadable primary button is worse than an unbranded one | Some brand colours are rejected |
| Domain colours | Defined once in layer 3 | Difficulty and status must mean the same thing on every surface | A registry to maintain |
| Dark mode | Designed independently, not inverted | Shadows, saturation, and scanned figures all behave differently | A second full palette to verify |
| Print | A separate mode, monochrome-safe | Question papers are photocopied | A third token set |

---

## 23. Notification System

### 23.1 Two Systems That Are Easy to Confuse

The word "notification" covers two mechanisms with almost nothing in common except that both put words in front of the user. Conflating them is the most common design mistake in this area, so the architecture keeps them physically separate.

The first is the **transient toast**: an ephemeral, in-session acknowledgement that something the user just did succeeded or failed. "Paper saved." "Export failed — retry?" It has no server record, no history, and no meaning ten minutes later. It lives entirely in the `toasts` store (§9) and is described in §23.3.

The second is the **durable notification**: a server-owned record that an event of lasting relevance occurred, addressed to a specific user, that persists until the user has seen and dismissed it. "Your paper *Term 2 Physics* finished generating." "You have been asked to approve *Unit 4 Test*." "This organisation is at 90% of its monthly quota." These survive reloads, cross devices, and appear in a notification centre with an unread count. They are described in §23.4 onward.

| Property | Transient toast | Durable notification |
|---|---|---|
| Owner | Client (`toasts` store) | Server (persisted per user) |
| Lifespan | Seconds, then gone | Until dismissed by the user |
| Survives reload | No | Yes |
| Cross-device | No | Yes |
| Has an unread count | No | Yes |
| Triggered by | The user's own action, locally | A server-side event, possibly caused by someone else |
| Delivery | Direct function call in the app | SSE `notification.created`, plus fetch on load |
| Example | "Copied to clipboard" | "*Term 2 Physics* is ready" |

The rule of thumb: **if the user caused it and would not care about it after leaving the page, it is a toast; if the system caused it or someone else did, and the user would want to find it later, it is a notification.** A long-running job the user started is the interesting boundary case — the user caused it, but its completion arrives minutes later, possibly on another device, so it is a durable notification (and, if the originating tab is still open, also a toast).

### 23.2 Notification Taxonomy

Durable notifications are typed, and the type drives icon, grouping, default politeness, and the action offered when the user opens it.

| Type | Origin | Example | Deep-links to |
|---|---|---|---|
| `job.completed` | Async pipeline | Paper generated, export ready | The produced artefact |
| `job.failed` | Async pipeline | Generation failed, export failed | The failed job, with retry |
| `approval.requested` | Workflow | You were assigned as approver | The paper's approval view |
| `approval.decided` | Workflow | Your paper was approved / changes requested | The paper, at the decision |
| `quota.warning` | Metering | Organisation at 80% / 90% / 100% | The usage dashboard |
| `system.announcement` | Platform | Maintenance window, feature notice | An announcement detail, if any |
| `membership.changed` | Admin action | Your role changed, you were added to a program | The relevant context |

The taxonomy is deliberately small and closed on the client. New types are additive; an unknown type received by an older client renders with a generic icon and its server-provided title rather than breaking (§23.11) — the server always sends a human-readable `title` and `body`, so a client that does not recognise the type can still display it.

### 23.3 The Transient Toast Layer

Toasts are the simplest notification surface and the one most likely to be misused, so its constraints are explicit.

The `toasts` store holds a small, bounded queue. Any part of the app can enqueue a toast through a single function; the shell renders the queue in a fixed region (bottom-right on desktop, bottom-centre above the tab bar on phones per §20.9). The store is client-only, never persisted, and cleared on org switch (§9) along with everything else.

Three constraints keep the layer honest:

- **The queue is bounded.** At most a handful of toasts are visible at once; excess is coalesced ("3 questions regenerated") or dropped oldest-first. A burst of SSE events must never produce a wall of toasts — this is the failure mode that trains users to ignore the region entirely.
- **Politeness is deliberate, per §21.6.** The ordinary toast is a `polite` live-region announcement. Only a toast that blocks or reverses the user's work — a save that failed, a session about to expire — is `assertive`. Success toasts are `polite` or, for trivially expected successes, not announced at all.
- **A toast is not a substitute for handling an error.** A failed mutation shows a toast *and* leaves the UI in a correct state (the optimistic update is rolled back, the form re-enabled, the retry affordance present). The toast is the announcement, not the recovery — recovery is §25's concern.

Toasts carry an optional single action ("Undo", "Retry", "View"). The action must be safe to miss, because the toast will auto-dismiss; anything the user *must* act on is a dialog or an inline error, not a toast.

### 23.4 Durable Notification Delivery

A durable notification reaches the client through two complementary paths, and the client must treat them as two views of one server-owned truth rather than two independent streams.

```
  Server event (job done, approval requested, quota crossed)
        │
        ▼
  Persist notification row (per recipient user, tenant-scoped)
        │
        ├──────────────► SSE  notification.created  ──► open clients (live)
        │
        └──────────────► (durable; fetched on next load) ──► GET /notifications
```

- **On load / reconnect:** the client fetches the current unread count and the first page of the notification list. This is the source of truth. Anything that happened while no client was connected is picked up here.
- **While connected:** the `notification.created` SSE event (§11) delivers new notifications live. The handler increments the unread count and, if the notification centre is open, prepends the item.

The two paths can race — an SSE event may arrive for a notification already included in the fetched page, or a reconnect fetch may return a notification the live stream also just pushed. The client **deduplicates by notification id** and treats the server's unread count from a fresh fetch as authoritative over any locally accumulated count. The count is never computed by adding SSE events to a stale base; a reconnect always re-reads it (§23.6).

Critically, **the unread count is served separately from the list.** The badge in the shell needs a single integer on every load; it must not require fetching a page of notifications the user has not opened. A dedicated lightweight endpoint returns the count, and TanStack Query owns it under an org-scoped key (`[orgId, 'notifications', 'unreadCount']`).

### 23.5 Live Delivery Sequence

```
Teacher   Shell (badge)   session/SSE   API      Notif store cache
   │            │              │          │              │
   │  (paper generation running in background)          │
   │            │              │          │              │
   │            │              │  worker persists notif  │
   │            │              │◄─────────┤              │
   │            │   notification.created  │              │
   │            │◄─────────────┤          │              │
   │            │   increment unreadCount cache (+1)     │
   │            │──────────────────────────────────────►│
   │            │   if centre open: prepend item         │
   │            │──────────────────────────────────────►│
   │   badge shows "1", optional polite toast            │
   │◄───────────┤              │          │              │
   │            │              │          │              │
   │  opens notification centre           │              │
   │───────────►│  GET /notifications (page 1)           │
   │            │──────────────────────►  │              │
   │            │◄──────────────────────  │              │
   │  reads item, clicks it               │              │
   │───────────►│  PATCH .../{id}/read   │              │
   │            │──────────────────────►  │              │
   │            │  optimistic: mark read, unreadCount −1  │
   │            │  navigate to deep-link (the artefact)   │
   │◄───────────┤              │          │              │
```

Two details in this flow matter. First, the SSE handler updates the **count** unconditionally but only touches the **list cache** when the centre is open — there is no reason to hold a growing list in memory for a panel the user never opened; the list is fetched fresh when opened. Second, marking-as-read is an optimistic mutation (§10) because it is safe and reversible: the count decrements immediately, and a failed `PATCH` rolls it back and re-flags the item.

### 23.6 Reconnect and Catch-Up

SSE connections drop — sleep, tunnels, network changes (§11). On reconnect the client does **not** attempt to replay missed `notification.created` events; that would require the client to track a cursor into an event stream it does not own. Instead, reconnection triggers the same catch-up as a cold load: re-fetch the unread count (authoritative) and invalidate the notification list so the next open is fresh.

This is the same bias to **invalidate rather than patch** established in §11 for all SSE-driven cache updates. The live event is an optimisation that keeps an open client current; correctness comes from the fetch. A user who was offline for an hour and reconnects sees the right count because it was read from the server, not reconstructed from events.

### 23.7 The Notification Centre and History

The notification centre is the durable list surface. It reuses the list machinery of §18 rather than inventing its own, because it is just another list of server records:

- **Cursor pagination**, not offset. The list mutates constantly (new items arrive at the head), so offset pagination would skip or duplicate rows as the user scrolls (§18.6). A cursor anchored to a stable ordering is correct under insertion.
- **Read/unread** is a filter facet: "All" or "Unread only".
- **Grouping** is by relative time (Today / Earlier this week / Older) purely for presentation; the underlying order is reverse-chronological.

The centre offers three actions, each an optimistic mutation:

| Action | Effect | Reversible on failure |
|---|---|---|
| Open a notification | Marks it read, decrements count, navigates to its deep-link | Yes — the count and read-flag roll back |
| Mark all read | Sets count to 0, flags the loaded set read | Yes — the whole batch rolls back |
| Dismiss | Removes it from the active list | Yes — re-inserted at its position |

"Mark all read" is the one action that can affect notifications not currently loaded in the client. It is therefore a **server operation over the whole unread set**, not a loop over loaded rows (the same distinction as "select all matching" in §18.11): the client sends one request, the server marks the user's entire unread set read, and the client sets the count to 0 optimistically and invalidates the list.

### 23.8 Quota Warnings — an Escalating Notification

Quota warnings (§12.9) are durable notifications with an escalation ladder, and they are worth calling out because they combine the notification system with a standing UI state.

```
  usage crosses 80%  ──►  notification (quota.warning, level=warn)
  usage crosses 90%  ──►  notification (quota.warning, level=high)   + standing banner
  usage reaches 100% ──►  notification (quota.warning, level=block)  + standing banner
                          + quota-gated actions disabled with explanation
```

Two things are happening. The **crossing** is an event — it fires a `quota.warning` SSE event and persists a notification, once per threshold, so a user is not re-notified on every subsequent request above 80%. The **state** — "this org is over quota" — is a property of the current session derived from the org's usage, surfaced as a standing banner and used to disable quota-gated actions.

The client never decides quota is exhausted on its own; it reflects the server's determination. A generate action attempted at 100% is disabled with an explanation ("hide by role, disable by state", §7), and if somehow attempted, the server returns a `429`/business `400` with an `ehRef` (§25) that the UI surfaces. The threshold levels (80/90/100) are server-driven so they can be tuned without a client release.

Because a quota warning is relevant to admins more than to individual teachers, its recipient set is a server concern (§12.9 routes it to `coaching_admin`); the client simply renders whatever notifications it is given.

### 23.9 Cross-Tab Behaviour

A user with three tabs open must not see three independent, diverging notification states. Per §9's cross-tab coordination:

- **The unread count is synchronised across tabs.** When one tab marks a notification read, the others reflect the new count without each having to receive its own SSE event or re-fetch. The leader tab (§9) owns the authoritative SSE connection; count changes are broadcast to followers over the same channel used for session coordination.
- **A `notification.created` event is handled once, not once per tab.** Only the leader holds the SSE connection, so the toast (if any) and the count increment happen a single time and propagate; three tabs do not each pop a toast for the same event.
- **Marking read is deduplicated.** If two tabs race to mark the same item read, the mutation is idempotent server-side and the count is reconciled from the server, so the result is correct regardless of ordering.

This is the same leader/follower model the session and SSE layers already use; the notification system rides on it rather than opening its own coordination channel.

### 23.10 Accessibility of Notifications

Notifications intersect directly with §21's live-region rules, and getting the politeness wrong is both an annoyance and an accessibility failure.

- **The unread badge** exposes its count to assistive technology as text, not colour alone (§21.9) — "Notifications, 3 unread", not a red dot.
- **Live toasts** are `polite` by default; only work-blocking ones are `assertive` (§21.6, §23.3). A stream of background job completions must not interrupt a screen-reader user mid-sentence.
- **The notification centre** is a standard focus-managed popover: focus moves into it on open, is trapped while open, and returns to the trigger on close (§21.5).
- **Deep-linking** from a notification moves focus to the destination's main heading (§21.5), so a keyboard or screen-reader user lands where the notification pointed rather than at the top of a new page.

### 23.11 Failure Handling

| Failure | Behaviour |
|---|---|
| SSE disconnected | Live delivery pauses; count and list catch up on reconnect via fetch (§23.6). No events are "lost" — they were persisted server-side |
| Unread-count fetch fails | Badge shows its last known value or is hidden; retried on the next query cycle. Never shows a fabricated count |
| Notification list fetch fails | The centre shows a retry state, not an empty list that implies "no notifications" |
| Mark-read mutation fails | Optimistic decrement rolls back; the item re-flags unread; a polite toast notes it |
| Unknown notification type | Rendered with a generic icon and the server-provided title/body; deep-link falls back to a safe default (§23.2) |
| Duplicate delivery (SSE + fetch) | Deduplicated by id; count reconciled from the server (§23.4) |
| Toast queue overflow | Coalesced or dropped oldest-first; never a wall of toasts (§23.3) |

The through-line: **the notification system fails quiet, not loud, and never fabricates state.** A count it cannot verify is hidden rather than guessed; a list it cannot fetch says "retry" rather than "nothing here"; a burst it cannot show gracefully is coalesced rather than dumped.

### 23.12 Extensibility

| Future capability | How the architecture accommodates it |
|---|---|
| New notification types | Additive to the taxonomy (§23.2); older clients degrade gracefully |
| Notification preferences (per-type opt-out) | A settings surface writing server-side preferences; delivery filtering stays server-side |
| Email / push digest of unread | A server concern reading the same notification store; the client is unaffected |
| Grouping by entity ("5 updates on this paper") | A presentation change over server-supplied grouping keys |
| Snooze / remind-later | A new mutation and a server-side scheduled re-delivery; the client adds one action |
| Read receipts across devices | Already implied — read state is server-owned, so it is consistent across devices today |

### 23.13 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Toasts vs notifications | Physically separate systems | They share nothing but words on screen; conflating them corrupts both | Two mechanisms to understand |
| Notification ownership | Server-owned, per-user, tenant-scoped | Must survive reload, cross devices, and reflect events caused by others | No purely client notifications of lasting relevance |
| Delivery | SSE for live + fetch for truth | Live keeps open clients current; fetch guarantees correctness after any gap | The two paths can race, so dedup is mandatory |
| Unread count | Served by a dedicated lightweight endpoint | The badge must not fetch a page the user never opened | One more endpoint |
| Catch-up | Re-fetch on reconnect, never replay events | The client does not own a cursor into the event stream | A brief window where an open client lags until reconnect |
| History pagination | Cursor, not offset | The list mutates at the head constantly | Cursor plumbing (already built for §18) |
| Mark-all-read | Server operation over the whole set | Affects items not loaded in the client | Cannot be a pure client loop |
| Quota warnings | Event (once per threshold) + standing state | Avoids re-notifying on every over-threshold request while keeping the banner live | Two representations of one condition |
| Cross-tab | Leader owns SSE; count broadcast to followers | One event, one toast, one count across N tabs | Depends on the §9 coordination layer |
| Failure posture | Fail quiet, never fabricate | A wrong count or a false "nothing here" erodes trust | Occasionally hides information rather than guessing it |

---

## 24. Security

### 24.1 The Frontend's Security Role

The single most important idea in this section is what the frontend is *not*: it is **not** a security boundary. Every rule that protects a tenant's data — who may read a paper, which organisation a request belongs to, whether a user may approve their own work — is enforced on the server by the dependency chain (get_current_user → get_org_context → require_roles → scope) backed by PostgreSQL row-level security. The client cannot be trusted, because the client runs on a machine the attacker controls: its JavaScript can be modified, its network calls forged, its stored state read and rewritten.

The frontend's security responsibilities are therefore narrower and different in kind:

- **Do not leak** — keep credentials and secrets out of reach of scripts and out of the shipped bundle.
- **Do not become the attacker's tool** — prevent the app from executing injected code (XSS), being used to forge requests (CSRF), or being framed to trick the user (clickjacking).
- **Do not mislead** — never show a user a capability the server will reject, and never assert authority (like an organisation id) the server should derive itself.
- **Fail closed** — when the client is unsure whether something is permitted, it hides or disables rather than optimistically allowing.

Everything below is an application of one of those four responsibilities. Where a control's real enforcement lives on the server, this section says so and cross-references [Backend Architecture](07_Backend_Architecture.md) rather than restating it.

### 24.2 Token Handling — the Central Decision

The token model (§6) is the highest-leverage security decision in the frontend, so its reasoning is worth stating in full here.

```
  Access token (JWT, ~15 min)          Refresh token (opaque UUID, ~7 days)
  ┌───────────────────────────┐        ┌──────────────────────────────────┐
  │ Held in memory only        │        │ httpOnly, Secure, SameSite cookie  │
  │ (a module variable /       │        │ Client JS cannot read it           │
  │  Zustand session store)    │        │ Sent automatically on refresh call │
  │ Never in localStorage      │        │ Single-use, rotated, reuse-detected│
  │ Never in a cookie          │        │ (§6)                               │
  │ Gone on tab close          │        │                                    │
  └───────────────────────────┘        └──────────────────────────────────┘
```

The two tokens are stored differently *on purpose*, and the split defends against two different attacks:

- **The access token is in memory, never in `localStorage` or a readable cookie.** Persistent, script-readable storage is exactly what an XSS payload exfiltrates. Keeping the access token in a memory variable means a successful XSS attack can use it only for as long as the compromised page is open, and cannot skim it out of storage on a later visit. The cost — the token is lost on reload and must be re-obtained — is paid by the silent refresh on load (§6), which is cheap and worth it.
- **The refresh token is an httpOnly cookie the client cannot read.** Because it is the long-lived credential, it must be the *least* exposed. `httpOnly` puts it entirely out of JavaScript's reach, so no XSS payload can read it at all; `Secure` keeps it off plaintext connections; `SameSite` blunts CSRF (§24.4). The client never sees this value, never puts it in a header, and never handles it — the browser attaches it automatically only on the refresh endpoint.

The consequence for the rest of the app: **no security decision ever depends on reading a token's contents in JavaScript beyond the non-authoritative claims the session store already decoded for UI projection (§6, §7).** The claims in the access token (`role`, `activeOrgId`) are used to *shape the UI*, never to *grant access* — the server re-derives and enforces them on every request.

### 24.3 Cross-Site Scripting (XSS)

XSS is the frontend's most consequential threat because a successful injection runs with the user's full authority inside the page. The defence is layered.

| Layer | Control | Why |
|---|---|---|
| Framework | React escapes all interpolated content by default | The default path is safe; you must go out of your way to be unsafe |
| Dangerous API | `dangerouslySetInnerHTML` is banned except through one audited sanitiser | The only way to inject raw HTML is named to be scary, and we gate it |
| Rich content | Textbook OCR text, question bodies, and any server-stored HTML pass through a sanitiser (allow-list, not deny-list) before render | This content originates from uploads and models — untrusted by §24.7 |
| Transport of state | Token in memory, refresh cookie httpOnly (§24.2) | Limits what a successful XSS can steal even if one layer fails |
| Policy | A Content Security Policy (§24.6) restricts script sources and inline execution | Defence in depth — a bypassed escape still cannot load attacker script |
| Dependencies | Supply-chain controls (§24.8) | Most XSS in practice arrives through a compromised dependency, not hand-written code |

The subtle point is that **rich domain content is untrusted content.** Question text can contain markup, textbook extractions contain arbitrary strings from OCR, and model output is not sanitised by virtue of coming from our own backend. Any place that renders stored content as anything other than plain text routes it through the single sanitiser, configured with an explicit allow-list of tags and attributes. There is no second sanitiser with different rules; one implementation, audited once.

### 24.4 Cross-Site Request Forgery (CSRF)

CSRF matters only where the browser attaches credentials automatically, and in this architecture that is exactly one place: the refresh cookie.

- **Bearer-token API calls are not CSRF-able.** The access token is attached explicitly as an `Authorization` header by the client, from a memory variable. A forged cross-site request cannot read that variable or set that header, so the ordinary API surface is structurally immune.
- **The refresh endpoint is the exception,** because its credential is a cookie the browser sends automatically. It is protected by `SameSite` on the cookie (a cross-site POST does not carry it) plus the standard double-submit / origin-check pattern on the endpoint itself, enforced server-side.
- **Any BFF route handler that relies on a cookie** (§24.5) inherits the same requirement: cookie-authenticated same-origin handlers apply CSRF protection; header-authenticated calls do not need it.

The design intent is to keep the CSRF-exposed surface as small as possible — one refresh endpoint — rather than spreading cookie authentication across the API and then defending all of it.

### 24.5 Where Server-Side Route Handlers Are (and Are Not) Used

Next.js App Router allows server-side route handlers (a backend-for-frontend layer). This architecture uses them **narrowly and only where a cookie is involved**, not as a general proxy in front of the FastAPI API — and never as the backend itself. The API is a separate Python service; a Next.js route handler is never the system of record and never talks to the database.

```
  Browser ──(Authorization: Bearer …)──► FastAPI API       (the common case)

  Browser ──(httpOnly refresh cookie)──► Next route handler ──► FastAPI API
                                          (only for token refresh / logout,
                                           where the cookie must be read
                                           server-side and never by JS)
```

The reasoning:

- **The default path is browser-direct to the API** with a bearer token. Routing every call through a Next handler would double the network hops, duplicate the guard logic's assumptions, and create a second place tenant scoping could go wrong.
- **A server handler is introduced only when the operation must touch the httpOnly cookie** — refresh and logout — because JavaScript cannot read that cookie by design (§24.2). The handler reads the cookie, calls the API, and sets/clears the cookie in the response.
- **Server handlers never hold secrets that grant tenant access.** They do not embed API keys or service credentials; they operate on the user's own cookie. There is no privileged backend identity sitting in the frontend deployment.

This keeps the frontend deployment free of standing secrets (§24.9) while still allowing the httpOnly-cookie pattern that makes §24.2 possible.

### 24.6 Content Security Policy and Framing

A Content Security Policy is the backstop that limits the damage of any escaping or injection failure: even if attacker-controlled markup reaches the DOM, a strict CSP prevents it from loading or executing attacker script.

| Directive intent | Posture |
|---|---|
| Script sources | Restricted to the app's own origin and explicitly allow-listed origins; no arbitrary inline script |
| The one inline script | The pre-paint theme script (§22.4) is the sole permitted inline script, admitted by nonce/hash — not by opening inline execution generally |
| Styles | App origin plus the controlled style pipeline; inline style constrained |
| Connect (fetch/SSE) | The API origin and object-storage origins used for direct upload/download (§17) |
| Frame ancestors | `frame-ancestors 'none'` (or an explicit allow-list) so the app cannot be framed — this is the anti-clickjacking control |
| Object / embed | Denied |
| Base URI / form action | Locked to the app origin |

Two things follow. First, the pre-paint theme script is the *only* inline script, which is why §22.4 treats it as a special case rather than a pattern to reuse — every additional inline script would either weaken the policy or need its own nonce. Second, **clickjacking is handled by `frame-ancestors`** (with `X-Frame-Options` as a legacy companion): the approval and generation surfaces, where a framed overlay could trick a user into approving a paper, must never be embeddable.

### 24.7 Treating External Content as Untrusted

The app renders content from several sources that are, from a security standpoint, all untrusted:

- **Uploaded files and their OCR/parsed output** — arbitrary bytes and arbitrary extracted text (§5, §17).
- **Model-generated question content** — the model can be prompted, directly or through poisoned source material, to emit markup or script-like strings.
- **Organisation branding assets** — a tenant-supplied logo and colours (§22.6).
- **Free-text fields** — names, titles, and descriptions entered by any user in any tenant.

The rule is uniform: **content is data, not instructions, and not markup.** It is rendered as text by default, sanitised through the single allow-list sanitiser when it must render as rich content (§24.3), and — for branding — contrast-gated and size/type-checked before use (§22.6, §17.9). A tenant's uploaded logo is never rendered in a way that could execute; a model's output is never `dangerouslySetInnerHTML`'d.

This mirrors the standing rule the platform applies everywhere: text arriving from files, model output, other users, or any external source is treated as data and never as an instruction to the client.

### 24.8 Client Never Asserts Authority

A recurring frontend security anti-pattern is letting the client name the tenant or the permission. This architecture forbids it.

- **The client never sends `org_id` as a request parameter to assert scope.** The active organisation is a claim in the server-verified access token (§6); the server derives scope from the token and enforces it with RLS. If the client could pass `org_id`, a modified client would pass someone else's. The `activeOrgId` the client holds is for *display and query-key partitioning* (§9), not for telling the server which tenant to act on.
- **Org switching is a server operation** (§6): the client requests a switch, the server verifies membership and mints a new token with the new `activeOrgId`. The client cannot switch tenants by editing local state.
- **Permission checks (`useCan`, `<Can>`, §7) are UI projection only.** They decide what to render, never what is allowed. The identical check runs server-side in `require_roles` and is the only one that counts. A user who forces a hidden button to appear still hits a `403`.
- **Separation of duties (BR-6) is enforced server-side.** The client hides the "approve" action from a paper's author, but the *rule* that an author cannot be the sole approver lives in the backend; the client's hiding is courtesy, not enforcement.

The one deliberate exception to "the client never asserts identity" is benign: the `['me']` query and the current-user bootstrap (§9), where the client asks "who am I?" and the server answers from the token — the client is not asserting, it is asking.

### 24.9 No Secrets in the Bundle

Everything shipped to the browser is public. The build therefore contains **no secrets of any kind**: no API signing keys, no service credentials, no third-party tokens that grant privileged access.

- Only values explicitly marked as public (the API base URL, the SSE endpoint, public feature flags, a client-side error-reporting DSN that is safe to expose) are embedded at build time.
- Anything sensitive lives server-side — in the FastAPI backend or, for the narrow cookie operations, in the Next route-handler runtime environment (§24.5), never in code sent to the browser. The backend's secrets are not merely unused by this app; they are unreachable from it, because `frontend/` and `backend/` are physically separate workspace roots ([ADR-015](ADR/ADR-015-workspace-root-split.md)) that do not share a dependency tree or an env file.
- Signed URLs for upload and download (§17.13, §17.15) are the model for privileged operations: the browser is handed a **short-lived, single-purpose capability** minted by the server, never the key that mints it. A signed URL is a bearer capability — treated with the same care as a token, kept out of logs and analytics, and never persisted (§17.13).

CI includes a secret-scanning step so a credential accidentally added to a public config or committed to the frontend repo is caught before it ships.

### 24.10 Dependency and Supply-Chain Security

Most real-world frontend compromise arrives through the dependency graph, not hand-written application code, so the supply chain is treated as part of the security surface.

| Control | Practice |
|---|---|
| Pinned versions | Dependencies are pinned via a committed lockfile; builds are reproducible, and an upstream version cannot change under us |
| Provenance | New dependencies are vetted for maintenance, popularity, and typosquatting before adoption; an unusual or look-alike package name is flagged |
| Vulnerability scanning | Automated audit of the dependency tree in CI; known-vulnerable versions block or warn per severity |
| Minimal surface | The always-dynamic-import list (§19.6) also limits how much third-party code is even present on any given route |
| Integrity | Subresource integrity / origin pinning for anything loaded from outside the app's own origin (kept minimal by the CSP connect/script rules) |
| Update discipline | Security updates are applied deliberately, not blindly auto-merged, so a compromised release cannot ride in through automation |

The always-dynamic-import discipline (§19.6) pays a security dividend as well as a performance one: heavy third-party engines (PDF, spreadsheet parsing, rich-text) are loaded only on the routes that need them, so a vulnerability in one of them is not present in the bundle of a user who never touches that feature.

### 24.11 Session and Logout Security

- **Idle and absolute session limits** are enforced by token lifetimes (§6): the ~15-minute access token and ~7-day refresh window bound how long a leaked credential is useful.
- **Refresh reuse detection** (§6) means a stolen refresh token, once used by the attacker, invalidates the whole session family on the next legitimate use — the theft is self-limiting and detectable.
- **Logout is a server operation**, not just clearing memory: it revokes the refresh token server-side and clears the httpOnly cookie via the route handler (§24.5). Clearing client memory alone would leave a valid refresh token in the cookie.
- **Org switch performs a hard client reset** (§9): all cached data for the previous organisation is discarded so no tenant's data lingers in memory or cache under another tenant's context.
- **On any `401` that refresh cannot resolve**, the client discards all in-memory auth state and routes to login — it never continues in a half-authenticated state.

### 24.12 Failure Handling

| Failure | Behaviour |
|---|---|
| Access token expires mid-session | Silent single-flight refresh (§6); the user sees nothing. On refresh failure, routed to login with work preserved where possible (§25) |
| Refresh token stolen and reused | Server reuse-detection revokes the session family; the legitimate user is logged out and must re-authenticate — the safe failure |
| XSS payload reaches the DOM | CSP blocks script execution; token in memory limits theft; refresh cookie is unreadable (§24.2, §24.6) |
| Forged cross-site request | Bearer calls immune; refresh/cookie endpoints protected by SameSite + origin checks (§24.4) |
| Attempt to frame the app | Blocked by `frame-ancestors` (§24.6) |
| Client forces a hidden action to appear | The server dependency chain returns `403`; the UI surfaces it (§24.8) |
| Modified client sends a foreign `org_id` | Ignored — scope comes from the token, enforced by RLS; the request is scoped to the real tenant (§24.8) |
| Dependency CVE disclosed | CI audit flags it; dynamic-import isolation limits exposure to affected routes (§24.10) |
| Signed URL leaks | Bounded by its short TTL and single purpose; it grants no lasting access and no minting key (§24.9) |

The unifying principle: **every client-side control is a convenience or a damage-limiter, and every real boundary is on the server.** When the two disagree — a tampered client, a forged request — the server wins and the client's misbehaviour is inert.

### 24.13 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Frontend as boundary | It is not one | The client runs on the attacker's machine; only the server can enforce | All access rules must be duplicated server-side (and are) |
| Access token storage | Memory only | Denies XSS a persistent credential to exfiltrate | Lost on reload; needs silent refresh |
| Refresh token storage | httpOnly Secure SameSite cookie | Least-exposed storage for the longest-lived credential; unreadable by JS | Requires a server route handler to touch it |
| BFF scope | Only cookie operations, not a general proxy | Keeps hops low and avoids a second place scoping can break | The refresh/logout path differs from the common path |
| CSRF surface | Minimised to the one cookie endpoint | Bearer calls are structurally immune; defend only what is exposed | Cookie endpoints need explicit CSRF protection |
| Rich content | Single allow-list sanitiser; default to plain text | Domain content (OCR, model output) is untrusted | One audited chokepoint everything must pass |
| Client authority | Client never asserts `org_id` or grants permission | A modified client would assert someone else's | UI must ask the server for identity rather than assume it |
| Secrets | None in the bundle; capabilities via signed URLs | Everything shipped is public | Privileged operations need a server round trip to mint a capability |
| Supply chain | Pinned, scanned, vetted, dynamically isolated | Most real compromise comes through dependencies | Update discipline is manual, not blind auto-merge |
| Failure posture | Fail closed | An unsure client should deny, not optimistically allow | Occasionally hides something the user could in fact do, until the server confirms |

---

## 25. Error Handling

### 25.1 One Through-Line

Error handling in this application is organised around a single non-negotiable principle, stated once so every rule below can be read as a consequence of it:

> **No failure path may discard the user's work.**

A teacher assembling a paper has invested attention and intent; a failed save, a dropped connection, or an expired session must never silently throw that away. This principle already appears in the builder (§13.20) and the upload layer (§17.14); §25 generalises it across every surface. Every decision that follows — where boundaries sit, how errors are classified, when the client retries, how it behaves offline — is downstream of "preserve the work."

### 25.2 The Error Taxonomy

Errors are not one thing, and treating them uniformly is why so many applications handle them badly. The client sorts every error into one of five classes, and the class determines the response.

| Class | Origin | User can fix it? | Client response |
|---|---|---|---|
| **Validation** | Server rejects input (`422`) or client Zod check | Yes, by correcting input | Inline, field-level messages; focus the first error (§16, §21.6) |
| **Business-rule** | Server rejects a valid-looking request (`400`, `409`) | Sometimes, by changing context | Explanatory message tied to the rule and its `ehRef` (§25.4) |
| **Authorization** | `401` / `403` | Rarely — it is a permission fact | `401` → refresh or re-login; `403` → explain, do not retry (§25.5) |
| **Transient** | Network, `429`, `502`, `503`, timeouts | Not directly; time or retry fixes it | Bounded retry with backoff; then a retry affordance (§25.6) |
| **Unexpected** | `500`, client exceptions, contract violations | No | Error boundary; report; offer recovery without blame (§25.7) |

The taxonomy matters because the *right* response differs completely by class. Retrying a `403` is pointless and confusing; retrying a `502` is correct. Showing a field-level message for a `500` is misleading; showing a full-page error for a single failed widget is disproportionate. The class is derived from the HTTP status and the error envelope's `code` (§10), not guessed from the message text.

### 25.3 The `ApiError` Shape

Every non-2xx response carries the standard error envelope (§10):

```
{
  success: false,
  error: {
    code:     "PAPER_LOCKED",          // stable, machine-readable
    message:  "This paper is being edited by someone else.",
    details:  { … },                   // optional, field errors etc.
    ehRef:    "EH-9"                    // maps to the platform error catalogue
  },
  meta: { requestId: "…" }             // for support / correlation
}
```

The API client (§10) parses this into a single typed `ApiError` object that the whole app understands. Three fields do distinct jobs:

- **`code`** drives *behaviour* — the client switches on it to decide class, whether to retry, and which recovery to offer. It is stable across releases; message text is not.
- **`message`** is the *default human-readable* text. The client may override it with a more contextual message for known codes, but it is always a safe fallback.
- **`ehRef`** ties the failure to the platform's error-handling catalogue (EH-1 … EH-18), so a message shown to a user, a log line, and the backend's documented handling of that condition all reference the same identifier.
- **`requestId`** is surfaced discreetly on unexpected errors so a user contacting support, and the team investigating, can correlate to the exact request in the logs (§26).

A response that does not match this envelope (a proxy error page, a truncated body) is itself treated as an `Unexpected` error rather than being force-parsed — the client never trusts a malformed error to be well-formed.

### 25.4 Mapping to the EH Catalogue

The backend defines a catalogue of handled error conditions (EH-1 through EH-18) spanning validation, business rules, quota, concurrency, upstream/LLM failures, and infrastructure. The frontend does not re-invent this; it **maps each `ehRef` to a presentation and a recovery**, so behaviour stays consistent with the documented backend contract.

| EH family (illustrative) | Condition | Frontend presentation | Recovery offered |
|---|---|---|---|
| Validation range | Malformed / invalid input | Field-level inline errors | Correct and resubmit |
| Business-rule range | Rule violation (e.g. separation of duties, BR-6) | Explanatory message naming the rule | Change context; no blind retry |
| Concurrency (e.g. EH-9) | Optimistic-lock conflict (`409`) | "Someone else changed this" with a refresh/merge path | Reload latest, re-apply intent (§13, §10) |
| Quota (e.g. quota EH) | Over limit (`429` / business `400`) | Quota explanation + link to usage (§23.8) | Wait, or an admin raises the limit |
| Upstream / LLM (e.g. EH for `502`) | Model or provider failure | "Generation couldn't complete" — not a stack trace | Retry the job server-side (§25.6) |
| Infrastructure | `500` / unavailable | Generic "something went wrong" + `requestId` | Retry; report; work preserved |

The exact numeric mapping lives with the shared error catalogue so a single change updates both sides; the client holds the *presentation and recovery* half of the contract, the backend holds the *detection and semantics* half.

### 25.5 Error Boundaries — Independent Failure Domains

Following §12.2, the UI is divided into **independent failure domains** so that one widget's crash never blanks the whole application.

```
  App shell (never inside a boundary — it must always render)
   │
   ├── Route boundary  ── a route's main content fails → route-level error state,
   │                       shell + nav still work, user can navigate away
   │
   ├── Section boundary ── a dashboard widget (chart, recent list) fails →
   │                       that card shows an error, siblings render normally
   │
   └── Feature boundary ── the builder's question list fails → the list shows an
                           error and a reload, the blueprint panel and header survive
```

The principles:

- **The shell is never inside a boundary.** Navigation, the notification tray, and the org context must survive any content failure — a user must always be able to leave a broken page.
- **Boundaries are placed at seams where partial failure is meaningful.** A single analytics chart failing to render should not take down the other five charts on the page; each independently-fetched, independently-rendered region gets its own boundary.
- **A boundary's error state is actionable, not terminal.** It says what failed at the right altitude, offers a retry or a way out, and — crucially — does not discard the surrounding state (§25.1). The builder's blueprint survives its question list crashing.
- **Boundaries catch render/exception failures; data-fetch failures are handled in-place** by the query layer's error state (§10). The two compose: a query error renders a friendly in-region message; an unexpected exception during render trips the boundary.

### 25.6 Retry and Recovery

Retry policy is derived from the taxonomy (§25.2), and it deliberately distinguishes what may be retried automatically from what requires the user.

- **Reads are retried automatically** with bounded exponential backoff on transient failures (network, `429`, `5xx`), because a `GET` is safe to repeat. After the bound is exhausted, the query surfaces a manual retry affordance rather than spinning forever.
- **Mutations are not blindly retried** (§10.9). A `POST` that timed out may have succeeded server-side; retrying could double-submit. The user is shown the failure and an explicit retry, and idempotency keys / server-side idempotency make the deliberate retry safe. The one architected exception is the idempotent upload-completion call, keyed on `uploadId` (§17.12).
- **`429` honours `Retry-After`.** The client waits the server-advertised interval rather than hammering, and communicates the wait rather than appearing frozen.
- **Long-running jobs retry server-side, not client-side.** A failed generation or export is re-triggered as a new job (`processing_failed` in §17.4 semantics), because the work lives on the server; the client re-issues the job request, it does not "retry" a connection.
- **`401` triggers exactly one single-flight refresh** (§6); concurrent failures wait on the one in-flight refresh rather than each launching their own. Success replays the queued requests transparently; failure routes to login.
- **`403` is never retried.** It is a fact about permission, not a transient condition; retrying only produces the same result and a worse experience.

### 25.7 Unexpected Errors and the Top-Level Boundary

Some failures are genuine bugs or contract violations — an exception during render, a response that violates the schema the client parses against (§10). These are the `Unexpected` class, and they are handled to be recoverable and diagnosable rather than silent or alarming.

- The nearest error boundary (§25.5) catches the exception and renders a calm, non-technical message — no stack trace, no blame, no raw error object.
- The `requestId` (or a client-generated error id when there is no request) is shown discreetly so support can correlate, and the error is reported to monitoring (§26) with enough context to reproduce.
- The user is offered a way forward: reload the failed region, navigate elsewhere via the still-live shell, or — where the failure touched unsaved work — recover from the local draft/autosave (§25.9).
- A schema-validation failure on a response is logged as a **contract violation** specifically, because it signals frontend/backend drift and is a class of bug worth surfacing distinctly to the team (§26).

### 25.8 Offline and Connectivity Handling

The network is assumed to be unreliable, especially for the tablet-and-phone monitoring/reviewing profiles (§20.1).

- **Loss of connectivity is a recognised state, not a cascade of individual errors.** The client detects offline/degraded conditions and surfaces one calm indicator rather than letting every in-flight request fail loudly and independently.
- **SSE disconnection is expected and handled by reconnect-with-catch-up** (§11, §23.6), not treated as an error the user must act on. A dropped stream shows a subtle "reconnecting" state, and correctness is restored by fetch on reconnect.
- **Reads degrade to cache.** TanStack Query serves the last-known data while offline and marks it stale; the user sees the app, clearly flagged as not-live, rather than a wall of errors.
- **Mutations while offline are refused clearly, not silently queued into ambiguity.** The user is told the action needs a connection and their input is preserved (§25.1) so they can submit when back online — the architecture does not pretend a write succeeded when it could not have.
- **Recovery on reconnect is automatic**: queries refetch, the SSE stream re-establishes and catches up, and the offline indicator clears without the user having to reload.

### 25.9 Work Preservation in Practice

Because §25.1 is the governing principle, the mechanisms that honour it are collected here:

| Situation | How work is preserved |
|---|---|
| Save fails (transient) | Optimistic state rolls back to a *correct* editable state; the user's edits remain on screen with a retry (§10, §13.20) |
| Session expires mid-edit | Silent refresh usually hides it; if re-login is needed, the local draft/autosave is retained and restored after auth (§6, §13.20) |
| Optimistic-lock conflict (`409`) | The user's intended change is held while the latest server version is loaded, so they re-apply rather than lose it (§25.4) |
| Browser reload / crash during edit | Autosaved builder draft is restored on return (§13.20); upload queue notes what cannot resume and why (§17.7) |
| Offline mutation | Refused with the input preserved for deliberate resubmission (§25.8) |
| Unexpected render crash in an editor | Boundary preserves surrounding state; draft recovery offered (§25.7) |

The consistent shape: the client **rolls back to a correct state, keeps the user's intent visible, and offers a deliberate path forward** — it never lands the user in a state where their work is gone and their only option is to redo it.

### 25.10 What the User Sees — Message Discipline

- **Messages are written for the person, not the log.** "This paper is being edited by someone else" — not "409 CONFLICT optimistic_lock". The technical detail (`code`, `requestId`, `ehRef`) is available for support but is not the headline.
- **The message matches the class** (§25.2): validation is inline and specific; business rules explain the rule; transient failures reassure and offer retry; unexpected failures apologise plainly and offer a way out.
- **No blame, no jargon, no dead ends.** Every error state offers at least one action — retry, go back, get help — so the user is never stranded.
- **Severity maps to politeness** (§21.6): a blocking error is announced assertively; a recoverable background failure is polite. This ties error handling to the accessibility contract rather than treating them separately.

### 25.11 Failure Handling Summary

| Failure | Class | Response |
|---|---|---|
| Field invalid | Validation | Inline message, focus first error |
| Rule violated (BR-6, etc.) | Business-rule | Explain the rule, no blind retry |
| Optimistic-lock `409` | Business-rule | Load latest, re-apply intent, preserve work |
| `401` expired | Authorization | Single-flight refresh; login if it fails, work preserved |
| `403` forbidden | Authorization | Explain; never retry |
| `429` over limit | Transient/quota | Honour `Retry-After`; quota explanation (§23.8) |
| Network / `502` / `503` | Transient | Bounded auto-retry (reads), explicit retry (mutations) |
| SSE dropped | Transient | Reconnect + catch-up; subtle indicator, no error |
| `500` / render crash | Unexpected | Boundary, calm message, `requestId`, recovery |
| Schema mismatch | Unexpected (contract) | Boundary + distinct contract-violation report |
| Offline | Transient (connectivity) | One calm indicator; reads from cache; mutations refused with work kept |

### 25.12 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Governing principle | No failure discards the user's work | Trust is lost permanently the first time effort vanishes | Every failure path must define its preservation behaviour |
| Classification | Five explicit classes from status + `code` | The correct response differs completely by class | A taxonomy to maintain and map |
| Error identity | Behaviour keys on stable `code`/`ehRef`, not message | Messages change; behaviour must not | Two-part contract shared with the backend |
| Boundaries | Independent failure domains, shell always outside | One widget must not blank the app or trap the user | More boundaries to place deliberately |
| Read retry | Automatic, bounded, with backoff | Reads are safe to repeat; transient failures self-heal | Backoff logic to get right |
| Mutation retry | Never blind; explicit + idempotency | A timed-out write may have succeeded | User does a manual retry for writes |
| `403` | Never retried | It is a permission fact, not a transient condition | None meaningful |
| Offline | A recognised state, not scattered errors | One calm indicator beats N loud failures | Explicit connectivity handling to build |
| Messages | Human-first, class-matched, always actionable | An error the user cannot act on is a dead end | Copy discipline and per-class presentation |
| Contract violations | Reported as a distinct class | They signal FE/BE drift worth catching early | Extra reporting category |

---

## 26. Frontend Monitoring

### 26.1 What Monitoring Is For

Monitoring exists to answer three questions the team must be able to answer at any time, ranked by urgency:

1. **Is the frontend broken right now, and for whom?** (Errors, crashes, failed requests, by route and tenant.)
2. **Is it fast enough for the work people are doing?** (The product metrics of §19.1 — time to interactive builder, time to first question card — plus Core Web Vitals.)
3. **Is the product being used the way we designed it?** (Which flows complete, which are abandoned, where users get stuck.)

The frontend cannot answer these from server logs alone, because the most important failures — a render crash, a layout that never becomes interactive, a user who abandons the builder — leave no trace on the backend. Client-side monitoring is how those become visible.

A standing constraint frames everything below: **monitoring must never itself become a privacy or security problem.** It captures behaviour and performance, not content. This is developed in §26.6.

### 26.2 The Three Signals

```
  ┌────────────────────────────────────────────────────────────────┐
  │  ERRORS            PERFORMANCE            PRODUCT ANALYTICS       │
  │  (Sentry-class)    (Web Vitals + custom)  (event stream)         │
  │                                                                  │
  │  crashes           TTI builder            flow started/completed │
  │  unhandled reject.  first question card    step abandoned         │
  │  API failures      LCP / INP / CLS        feature used           │
  │  contract viol.    route transition       conversion funnels     │
  │  boundary trips    long tasks             (no PII, no content)   │
  └────────────────────────────────────────────────────────────────┘
        │                    │                        │
        └──── correlated by requestId + release + session ──────┘
```

The three signals are captured separately but **correlated by shared keys** — `requestId` (§25.3), release version, and an anonymous session id — so an error, the performance around it, and the user action that triggered it can be viewed together. Without correlation each signal is an island; with it, an investigator can start from a spike in errors and see both what the user was doing and how the app was performing when it happened.

### 26.3 Error Monitoring

Error monitoring is the direct partner of §25. Every error class that reaches the `Unexpected` bucket, and selected others, is reported:

| Reported | Why |
|---|---|
| Uncaught exceptions and unhandled rejections | The baseline crash signal |
| Error-boundary trips (§25.5) | A boundary caught something — we want to know what and where |
| Schema/contract violations (§25.7) | FE/BE drift; a distinct, high-signal category worth its own alerting |
| API failures by `code`/`ehRef` (§25.3) | Distinguishes a backend outage from a frontend bug from an expected business rule |
| Failed silent refresh / auth loops (§6) | Auth breakage is invisible to the backend but painful to users |

Each report carries release version, route, anonymised session, `requestId` where present, and a breadcrumb trail of recent user actions — enough to reproduce, nothing that identifies the person or exposes tenant data (§26.6). **Expected business-rule errors (`400`/`403`/`422`) are counted, not alerted** — they are normal application behaviour; alerting on them would bury the signal that matters. Sampling and rate-limiting prevent one broken client from flooding the pipeline (§26.7).

### 26.4 Performance Monitoring — Field, Not Just Lab

The performance budgets of §19 are enforced in the *lab* (CI, on every build). Monitoring adds the *field*: what real users on real devices and networks actually experience, which no CI run can predict.

- **Core Web Vitals** (LCP, INP, CLS) are collected from real sessions and segmented by route and device class, because a p75 that looks fine in aggregate can hide a slow authenticated route on mid-range hardware.
- **The product-specific metrics of §19.1** — time to interactive builder, time to first question card — are the ones the team watches most, because they map to the work users came to do. These are custom marks, not standard vitals, and they are the frontend's own SLIs.
- **Long tasks and main-thread blocking** are sampled, because §19.9's main-thread protection is only verifiable in the field.
- **Regressions are caught by trend, not by a single number.** Field metrics are noisy; the signal is a sustained p75 shift after a release, which is why release version is a correlation key (§26.2).

The relationship is deliberate: **CI budgets prevent known regressions from shipping; field monitoring catches the regressions CI could not model.** Neither replaces the other.

### 26.5 Product Analytics

Product analytics answers "is the product used as designed", and it is scoped tightly to *behaviour*, never *content*.

- **Events are the completion and abandonment of the flows the architecture is built around**: paper generation started/completed/abandoned, blueprint edited, approval requested/decided, export produced, upload completed/failed. These map directly to the feature modules of §12.
- **Funnels** reveal where users drop out — if many teachers open the builder but few reach a generated paper, that is a design signal the raw error rate would never show.
- **What is never captured:** question text, paper content, textbook material, student or personal data, search queries containing free text, or anything a tenant would consider their data (§26.6). An event records *that* a paper was generated and its non-identifying shape (question count, subject taxonomy id), never *what* was in it.

### 26.6 Privacy and Security of Telemetry

This is the constraint that governs all three signals, and it is strict because telemetry is a classic place for tenant data to leak out of the boundary §24 works to maintain.

| Rule | Consequence |
|---|---|
| No content in telemetry | Question text, textbook material, paper bodies, and free-text fields are never sent to any monitoring endpoint |
| No PII | Names, emails, and student data are never event properties; users are an anonymous, rotating session id |
| No secrets or capabilities | Tokens and signed URLs (§24.9) are scrubbed from error payloads, breadcrumbs, and URLs before send |
| Tenant identity minimised | Where a tenant dimension is needed for triage it is an opaque org id, not a name, and only where it adds diagnostic value |
| Scrub before send | The error client redacts known-sensitive fields (auth headers, query params, request bodies) at the source, not at the destination |
| Respect the boundary | Telemetry endpoints are in the CSP `connect` allow-list (§24.6); nothing is exfiltrated to an unlisted origin |

The default is **opt-out of capture, not opt-in to scrubbing**: a field is not sent unless it is known-safe, rather than sent unless it is known-sensitive. This is the same fail-closed posture as §24.12, applied to observability.

### 26.7 Cost, Volume, and Sampling

Monitoring that captures everything from every session is expensive and, past a point, useless — noise drowns signal.

- **Errors are captured comprehensively but de-duplicated and rate-limited** so a single looping client cannot generate thousands of identical reports or inflate cost.
- **Performance and analytics are sampled** at a rate that yields statistically meaningful p75s without recording every session; the sample rate is a deployment configuration, not a code change.
- **Volume never scales with tenant data.** Per §19.11's scalability rule, monitoring cost is a function of active users and sessions, not of how many papers or textbooks a large tenant holds.
- **Sampling is consistent within a session** so a sampled-in session's error, performance, and analytics can still be correlated (§26.2) rather than arriving in fragments.

### 26.8 From Signal to Action

Monitoring is only worth its cost if it drives response:

- **Alerting is reserved for the unexpected and the systemic** — a spike in boundary trips, a collapse in refresh success, a sustained Web Vitals regression after a release. Expected business errors and single-user transient failures do not page anyone (§26.3).
- **Release version on every signal** makes the first question after any regression — "did the last deploy cause this?" — answerable in seconds, and enables fast rollback decisions.
- **`requestId` correlation** lets a frontend error be traced to the exact backend request and log line (§25.3), so FE and BE investigators are looking at the same event rather than guessing across a boundary.

### 26.9 Failure Handling

| Failure | Behaviour |
|---|---|
| Monitoring endpoint unreachable | Reports are buffered briefly then dropped; **the app never degrades because telemetry failed** |
| Telemetry SDK throws | Isolated so it can never crash the app it is observing — monitoring is strictly non-critical path |
| A field slips through scrubbing | Server-side scrubbing at the monitoring backend is a second layer; the primary defence is source-side redaction (§26.6) |
| Report volume spikes | Rate-limiting and dedup cap cost and noise (§26.7) |
| User blocks the telemetry origin | The app functions fully; monitoring simply has less data for that session |

The non-negotiable: **monitoring is never on the critical path.** A user's ability to generate a paper does not depend on whether an analytics event was delivered.

### 26.10 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Three separate signals | Errors, performance, analytics — correlated, not merged | Each answers a different question; correlation keys reunite them | Three pipelines and a correlation scheme |
| Field + lab performance | CI budgets *and* real-user monitoring | CI models known regressions; the field reveals the rest | Two performance systems to run |
| Product SLIs | Time-to-builder and time-to-first-question as primary | They map to the actual work, unlike generic vitals alone | Custom instrumentation to maintain |
| Telemetry privacy | No content, no PII, scrub-before-send, fail-closed | Telemetry must not breach the boundary §24 protects | Less data than a permissive tool would collect |
| Alerting scope | Unexpected/systemic only; count the expected | Alert fatigue destroys the value of alerts | Some real issues surface as trends, not pages |
| Sampling | Sample perf/analytics, capture+dedup errors | Full capture is costly and noisy past a point | Rare events may be under-sampled |
| Criticality | Monitoring is never on the critical path | Observability must not endanger the thing observed | Occasional data loss when telemetry fails |

---

## 27. Testing Strategy

### 27.1 What Testing Must Buy

Tests are not written for coverage numbers; they are written to buy confidence in the specific things this application must never get wrong. The strategy is organised around risk, so the heaviest testing lands where a bug is most costly:

1. **Authorization and tenant isolation must not regress** — a UI that shows one tenant's data to another, or an action to a user who should not have it, is the worst class of bug (§7, §24). These get the most deliberate coverage.
2. **The paper builder must not lose or corrupt work** — the §25.1 principle is a testable promise (§27.6).
3. **Money-and-trust flows must behave** — approval with separation of duties (BR-6), quota enforcement, export correctness.
4. **The contract with the backend must hold** — the envelopes, error codes, and event shapes the client parses (§10, §11).

Everything else is tested proportionally. The goal is a suite that fails loudly when one of the above breaks and stays quiet — and fast — otherwise.

### 27.2 The Testing Pyramid, Applied

```
              ╱╲          E2E (few) — critical journeys, real browser
             ╱  ╲             login → build → generate → approve → export
            ╱────╲
           ╱      ╲       Integration (more) — a feature module against a
          ╱        ╲          mocked API contract; routing, guards, forms,
         ╱──────────╲         cache behaviour, SSE handling
        ╱            ╲
       ╱              ╲    Component (many) — a component in isolation:
      ╱                ╲       states, variants, a11y, interaction
     ╱──────────────────╲
    ╱                    ╲  Unit (most) — pure logic: Zod schemas, query-key
   ╱______________________╲    builders, descriptor normalisation, mappers
```

The shape is deliberate and matches where value and cost sit:

- **Unit tests are the base** because the app has a lot of pure logic worth testing cheaply: Zod schemas (§16), the URL↔descriptor↔query-key transforms (§18.4), the SSE-event-to-cache-action mapping (§11), the `ehRef`→presentation mapping (§25.4). These are fast, deterministic, and catch the highest proportion of bugs per second of runtime.
- **Component tests** verify each shared and feature component across its states — including its accessibility and its "hide by role / disable by state" behaviour (§7) — in isolation.
- **Integration tests** are where most *confidence* is bought, because they exercise a feature module the way it actually runs: routing, guards, forms, the query cache, and SSE handling, against a **mocked API that honours the real contract** (§27.4).
- **End-to-end tests are few and precious**, reserved for the handful of journeys whose breakage would be a genuine incident (§27.5).

### 27.3 Unit and Component Testing

**Unit testing targets pure, high-value logic:**

| Target | Why it is unit-tested |
|---|---|
| Zod schemas (§16) | They are the validation contract; a loosened schema is a silent hole |
| Query-key builders (§9) | A wrong key causes cache leakage across tenants or stale reads |
| Descriptor normalisation (§18.4) | The stale-response-race defence depends on canonical keying |
| SSE→cache action map (§11) | Deciding patch-vs-invalidate wrong corrupts displayed state |
| `ehRef`/error mapping (§25.4) | The FE half of the error contract |
| `useCan` projection (§7) | Wrong projection shows or hides the wrong controls |

**Component testing** renders a component and asserts behaviour, not implementation. Every shared component is tested across its states (loading, empty, error, populated, disabled), its variants, its keyboard operability and ARIA (§21 — accessibility is a test assertion, not a hope), and its role/state gating. Tests query the DOM the way a user or assistive technology would (by role and accessible name), which means an accessibility regression *fails a functional test*, tying §21 to the suite structurally.

### 27.4 Integration Testing Against the Contract

Integration tests are the strategy's centre of gravity, and their defining choice is **how the API is mocked**: against the *real contract*, not against whatever shape is convenient.

- The mock returns the actual envelopes of §10 (`{success, data, meta}` / `{success:false, error:{code, ehRef}}`), the real status codes, and the real event shapes for SSE. A test that passes against a fictional response shape provides false confidence.
- The **same schema definitions the app parses with (§16) are used to validate the mocks**, so a mock cannot drift from what the client actually expects — if the contract changes, the schema changes, and both the app and its tests move together.
- Integration tests cover: routing and the guard/redirect behaviour (§5, §6), org-scoped cache partitioning and the org-switch hard reset (§9), form submission with server validation mapping (§15, §16), optimistic update and rollback (§10), SSE-driven cache updates and reconnect catch-up (§11, §23.6), and error-boundary behaviour per failure domain (§25.5).

The contract itself is guarded separately (§27.7) so that "the mock matches the client's expectation" and "the client's expectation matches the real backend" are both verified — the mock cannot silently lie about what the backend does.

### 27.5 End-to-End Testing

E2E tests run the real app in a real browser against a real (test) backend, and they are kept **deliberately few** because they are slow, flakier, and expensive to maintain. They are spent only on journeys whose failure is an incident:

- **Auth journey:** login, silent refresh across an access-token expiry, org switch with the hard reset, logout.
- **The core value path:** create a blueprint → generate a paper → watch live progress over SSE → review → approve (respecting BR-6) → export.
- **Upload journey:** the three-step signed-URL handshake through to `ready` (§17), including a resumable-transfer path.

E2E tests assert the journey completes and the critical guarantees hold (a second tenant cannot see the first's paper; an author cannot be the sole approver); they do not re-test every field and state — that is the layers below. Their flakiness is managed by testing against stable states and real async completion (waiting on the actual SSE event or job completion), never fixed sleeps.

### 27.6 Testing the Work-Preservation Promise

Because §25.1 ("no failure discards the user's work") is the application's governing promise, it is tested as a first-class requirement rather than assumed:

| Scenario under test | Asserted outcome |
|---|---|
| Save fails mid-edit | Optimistic state rolls back to an editable state; edits remain; retry present (§25.9) |
| Session expires while editing | After refresh/re-login, the draft is restored (§13.20, §6) |
| `409` optimistic-lock conflict | Latest loads; the user's intended change is not discarded (§25.4) |
| Reload during a build | Autosaved draft restored (§13.20) |
| Offline mutation attempt | Refused clearly; input preserved (§25.8) |
| Upload interrupted and resumed | Resume via `uploadId`; no full re-upload where avoidable (§17.7) |

These are integration tests, because work preservation is a behaviour of the feature module plus the cache plus the auth layer working together — exactly the seam integration tests cover.

### 27.7 Guarding the Frontend/Backend Contract

The single most dangerous silent failure is FE/BE drift — the client parsing a shape the backend no longer sends. Three mechanisms guard against it:

- **Shared schema source.** The envelope, error, and event shapes are defined once and shared (§10, §16); the client parses against them and tests validate mocks against them. Drift becomes a type/schema error, not a runtime surprise.
- **Contract-violation reporting in production** (§25.7, §26.3) turns any drift that does slip through into a distinct, alerted signal rather than a vague crash.
- **Contract tests** verify the shared schemas against the real backend's responses (in CI against a running test backend), closing the loop so the mock used in §27.4 provably matches reality.

### 27.8 What Is Deliberately Not Heavily Tested

An honest strategy names what it under-invests in, so the omissions are choices rather than gaps:

- **Exhaustive visual regression** across every component and viewport is not attempted; a targeted set of critical layouts is snapshot-tested, and the rest relies on the token system (§22) and component tests. Full visual regression is high-maintenance for modest additional confidence.
- **Third-party libraries are not re-tested.** The DnD engine, chart, and PDF libraries (§19.6) are trusted at their pinned versions (§24.10); tests cover *our* integration with them, not their internals.
- **Backend logic is not re-implemented in frontend tests.** The client tests that it *sends the right request and handles the response*, not that the backend enforces the rule — that is the backend's own suite. The frontend asserts it *surfaces* a `403`, not that the dependency chain produces one.

### 27.9 Tests in CI

- The unit/component/integration layers run on **every change**, fast enough to gate merges; a failure blocks the merge.
- **Accessibility (axe) and the performance budgets of §19.10** run in CI as described in their sections, so a11y and bundle-size regressions fail the build like any other test.
- **E2E and contract tests** run on a cadence appropriate to their cost — on the path to release and on a schedule — rather than on every commit, because their runtime would otherwise slow every change.
- **Secret scanning** (§24.9) and **dependency audit** (§24.10) run in CI as security gates alongside the functional suite.

### 27.10 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| Organising principle | Test by risk, not by coverage | Confidence should concentrate where bugs are costliest | Coverage numbers are not the target |
| Pyramid shape | Many unit, fewer integration, few E2E | Matches cost and value; keeps the suite fast | Requires discipline to resist E2E sprawl |
| Center of gravity | Integration against the real contract | Bugs live in the seams between routing, cache, forms, SSE | Mocks must be kept honest to the contract |
| Mock fidelity | Validate mocks with the app's own schemas | A mock that drifts gives false confidence | Schema sharing must be maintained |
| Accessibility | Asserted as functional tests (by role/name) | Ties §21 to the suite; a11y regressions fail loudly | Tests must query semantically, not by test-id alone |
| Work preservation | Tested as a first-class promise | §25.1 is a guarantee, not an aspiration | Several deliberate failure-injection tests |
| Contract drift | Shared schema + contract tests + prod reporting | The most dangerous failure is silent drift | A contract-test harness against a real backend |
| Under-investment | Named explicitly (visual regression, lib internals) | Honest scope beats false comprehensiveness | Some classes of bug rely on other layers |

---

## 28. Future Enhancements

### 28.1 How to Read This Section

This section is not a roadmap or a commitment; it is a statement that the architecture was designed with specific extensions in mind, and a record of *where each would attach* so a future team does not have to reverse-engineer the seams. Each entry names the capability, the architectural affordance that already accommodates it, and the honest cost or risk of building it.

The organising claim is that **the expensive structural decisions have already been made** — feature-module boundaries (§12), the single API/envelope contract (§10), server-owned state with org-first cache partitioning (§9), the token/guard model (§6, §7), the token-layered theme (§22), and the SSE event stream (§11). Most enhancements below are additive against those seams rather than rewrites of them.

### 28.2 Real-Time Collaboration on a Paper

Today the builder handles concurrent editing with optimistic locking and `409` conflict resolution (§25.4) — correct, but single-author at a time. Multi-user live collaboration (two teachers editing one paper, seeing each other's cursors and changes) is the most-requested plausible extension.

- **What already fits:** the SSE stream (§11) is one direction of the needed transport; the paper-builder store (§9) already isolates builder state; the event taxonomy already carries paper-level events.
- **What it demands that does not exist yet:** a bidirectional channel (SSE is server→client only, so this needs WebSocket or a companion upstream path), a conflict-resolution model stronger than optimistic locking (CRDT or OT), and presence. This is a genuine architectural addition, not a seam extension — it is called out precisely so it is not mistaken for a small feature.
- **Honest cost:** high. The §25.1 work-preservation guarantee becomes materially harder under true concurrency, and the collaboration model would need its own design document.

### 28.3 Offline-Capable Authoring

§25.8 handles offline gracefully (degrade to cache, refuse writes with work preserved) but does not permit *authoring* offline. A future step is a service-worker-backed offline mode for the builder.

- **What already fits:** autosaved local drafts (§13.20) are already the substrate; TanStack Query's cache is already the read layer.
- **What it demands:** a service worker, a durable local write queue with conflict reconciliation on reconnect, and a clear UI language for "not yet synced". The mutation-safety rules (§10.9, §25.6) and idempotency keys (§25.6) are the foundation the sync layer would build on.
- **Honest cost:** medium-high, concentrated in the reconciliation logic — the same class of problem as §28.2, at lower intensity.

### 28.4 Deeper AI-Assisted Authoring

The pipeline (retrieval → AI gap-fill → assembly) is server-side today (§06 AI Architecture). Richer in-editor AI assistance — inline "improve this question", "generate a variant", "explain this answer" — is a natural surface-level extension.

- **What already fits:** the streaming/SSE machinery (§11) already renders progressive AI output (`paper.question.ready`); the request/envelope contract (§10) already carries async jobs (`202`); the always-dynamic-import discipline (§19.6) means heavier assist UI loads only where used.
- **What it demands:** mostly new feature-module surface and new event types — additive by §12 and §23.2's open taxonomy. No structural change.
- **Honest cost:** low-to-medium, and the lowest-risk item here because it rides existing rails.

### 28.5 Analytics and Insight Surfaces

Analytics today is daily rollups surfaced in dashboards (§12). Richer, more interactive insight (drill-down, custom date ranges, cohort comparison) extends this.

- **What already fits:** the list/filter machinery (§18), the charting lazy-load (§19.6), and the server-owned rollup model. The rule that **no client cost may scale with total tenant data** (§19.11) is the binding constraint any richer analytics must still honour — aggregation stays server-side.
- **Honest cost:** low structurally; the discipline is resisting the temptation to pull raw data client-side for "flexibility".

### 28.6 Internationalisation and Localisation

The architecture does not ship i18n today but does not preclude it, and several decisions were made to keep the door open.

- **What already fits:** server-provided human-readable messages (§25.10) mean the client is not the sole source of copy; the theme's font tokens are already a layer-1 concern that §22.11 notes can become locale-aware; semantic HTML and the accessibility layer (§21) are locale-agnostic.
- **What it demands:** a message-catalogue layer, locale-aware formatting for dates/numbers/marks, and RTL support (which the token/layout system can express but which needs deliberate testing). Right-to-left is the highest-risk part because it touches layout, not just strings.
- **Honest cost:** medium, front-loaded — retrofitting i18n is far costlier than the modest ongoing discipline of not hard-coding strings, which is why it is noted now.

### 28.7 Native and Mobile Surfaces

The responsive design (§20) makes the web app usable on tablets and phones for reviewing and monitoring, deliberately not for heavy authoring. A dedicated mobile surface (PWA install, or native) could deepen the monitoring/reviewing profile.

- **What already fits:** the API contract (§10) and SSE stream (§11) are client-agnostic; the responsive monitoring/reviewing profiles (§20.1) already define what mobile should do well; a PWA is the smallest step and reuses the entire codebase.
- **Honest cost:** a PWA is low; a separate native app is high and would duplicate the client contract — noted so the cheap path (PWA) is chosen deliberately over the expensive one unless there is a real reason.

### 28.8 Extension Points Already Built In

Several affordances exist purely so future work does not require touching the core. This table consolidates the "how it extends" rows scattered through earlier sections:

| Seam | Extends without core change | Established in |
|---|---|---|
| Feature-module boundary + `index.ts` surface | New features are new modules | §12 |
| Open SSE event taxonomy | New real-time events degrade gracefully on old clients | §11, §23.2 |
| Open notification taxonomy | New notification types render generically if unknown | §23.2, §23.12 |
| Token layering (primitive→semantic→domain) | New modes, per-org themes, new domain colours | §22.11 |
| List descriptor + facet model | New filters, saved views, new list surfaces | §18.15 |
| Signed-URL capability model | New object types, new storage backends | §17.13, §24.9 |
| Async-job `202` + progress events | New long-running operations reuse the progress UI | §17, §11 |
| Role/permission projection | New roles are new projections, enforced server-side | §7 |

### 28.9 What Should *Not* Change Lightly

Extensibility cuts both ways — some decisions are load-bearing enough that changing them should require a deliberate architecture review, not a feature PR:

- **The frontend is not a security boundary** (§24.1). No enhancement may move an access decision to the client for convenience.
- **The client never asserts `org_id`** (§24.8). No feature may pass tenant scope as a parameter, however tempting for a cross-tenant admin view — that stays a server-derived, server-enforced concern.
- **No client cost scales with total tenant data** (§19.11). Richer analytics or search must keep aggregation and ranking server-side.
- **No failure discards the user's work** (§25.1). New editing surfaces inherit this promise; they do not get to opt out of it.
- **Server-owned state stays server-owned** (§9). New state is client-only *or* server-owned deliberately; the two are not blurred.

These are recorded here so a future contributor understands that crossing one of these lines is a decision with a cost, not an oversight to be casually corrected.

### 28.10 Design Decisions

| Decision | Choice | Reasoning | Trade-off |
|---|---|---|---|
| This section's role | Attachment points, not a roadmap | Preserves design intent without over-committing | Not a schedule or a promise |
| Bias | Most enhancements are additive against existing seams | The costly structure was decided up front | A few (collaboration, offline authoring) are genuine additions, named as such |
| Honesty about cost | Each entry states its real cost/risk | "Easy to add" is how architectures rot | Some desirable features are openly expensive |
| Invariants | A named set that must not change lightly | Extensibility must not erode the load-bearing decisions | Constrains future flexibility on purpose |

---

## 29. Sequence Diagrams

The preceding sections each described a slice of the system in isolation: how a token is refreshed (§6), how an SSE stream is consumed (§11), how an upload is brokered (§17), how a notification arrives (§23), how a conflict is surfaced (§25). This section stitches those slices into the small number of **end-to-end journeys** that actually matter — the ones a new engineer needs to hold in their head to understand how the frontend behaves as a whole.

These diagrams are deliberately *consolidating* rather than *duplicating*. Where §11, §17, and §23.5 already drew a focused sequence for a single mechanism, this section shows how those mechanisms compose across a complete user-visible flow, and cross-references the narrower diagram for the mechanical detail. If a step here feels compressed, the section reference beside it is where the full treatment lives.

### 29.1 How to Read These Diagrams

Each diagram uses the same participant vocabulary so they can be read together:

| Participant | What it stands for | Section of record |
|---|---|---|
| **User** | The human at the browser | — |
| **UI** | React components + hooks (the rendered app) | §14 |
| **Query** | TanStack Query cache + its fetcher | §9, §10 |
| **Session** | The in-memory session store + refresh guard | §6, §9.4 |
| **Fetch** | The single API client wrapper (envelope parse, error mapping, refresh trigger) | §10.4 |
| **BFF** | Next.js route handlers that own the refresh/logout cookie only | §24.5 |
| **API** | The FastAPI backend behind the dependency chain | §7, [07_Backend_Architecture.md](07_Backend_Architecture.md) |
| **SSE** | The Server-Sent Events channel (server→client) | §11 |
| **Store (obj)** | Object storage reached by signed URL | §17 |

Two conventions hold throughout:

- **Time flows down.** A line returning to a participant to its left is a response; a line continuing right is a forward call.
- **Dependency chain is elided.** Every `→ API` call implicitly passes `get_current_user → get_org_context → require_roles → scope` (§7). It is drawn explicitly only in §29.2, where it is the subject; elsewhere assume it and read a `403`/`401` return as "a dependency rejected this."

### 29.2 Authentication Lifecycle — Login, Silent Refresh, Org Switch

This is the diagram everything else depends on, because every other journey assumes a valid access token in memory. It composes three flows that share one machine: the initial login, the transparent refresh that happens ~every 15 minutes mid-session, and the org switch that re-mints a token against a new tenant. The mechanical single-flight detail lives in §6.7; here it is shown in context so the reader sees *when* each path fires.

```
LOGIN (cold start, no session in memory)
User    UI       Session     Fetch      BFF          API
 |  creds |          |          |          |            |
 |------->|          |          |          |            |
 |        |-- login(creds) ---->|          |            |
 |        |          |          |-- POST /auth/login -->|            (no guard: public)
 |        |          |          |          |            |-- verify creds, mint pair
 |        |          |          |          |<-- 200 {access} + Set-Cookie: refresh (httpOnly) --|
 |        |          |<-- access token (memory only) ---|            |
 |        |          |          |          |   (refresh cookie is invisible to JS — §24.2)
 |        |<- session ready ----|          |          |            |
 |<- app shell (role-scoped) ---|          |          |            |

SILENT REFRESH (access token near expiry OR a call returns 401)
UI      Query      Session          Fetch          BFF            API
 |  GET /papers    |                 |              |              |
 |---------------->|-- fetch ------->|              |              |
 |        |        |                 |-- GET /papers (Bearer) --------------------->|
 |        |        |                 |<----------------------- 401 token expired ---|
 |        |        |<- refresh needed |              |              |
 |        |        |  [single-flight guard: first caller refreshes, rest await]    |
 |        |        |-- refresh() --->|              |              |
 |        |        |                 |-- POST /auth/refresh (cookie sent by browser) ->|  via BFF (§24.5)
 |        |        |                 |              |-- rotate refresh, mint access ->|
 |        |        |                 |<-- 200 {access} + Set-Cookie: new refresh ----|
 |        |        |<- new access ---|              |     (old refresh now single-use-spent — §6.5)
 |        |        |-- retry GET /papers (new Bearer) ---------------------------->|
 |        |        |<------------------------------------------------ 200 {data} --|
 |<- data |        |                 |              |              |
   (the 401→refresh→retry is invisible to the user and to the calling component)

ORG SWITCH (user picks a different org from the switcher)
User    UI        Session       Fetch        API              Query
 | pick org B      |             |            |                 |
 |--------------->|              |            |                 |
 |        |-- switchOrg(B) ----->|            |                 |
 |        |        |-- POST /auth/switch-org {orgId:B} -------->|   (server checks membership in B)
 |        |        |             |            |-- mint access w/ activeOrgId=B
 |        |        |<------------ 200 {access for org B} -------|
 |        |        |  [HARD RESET — §9.9]                       |
 |        |        |-- clear query cache, close SSE, reset stores ------------->|
 |        |<- remount under org B ------------|                 |
 |<- org B shell --|              |            |                 |
   (no client-side data from org A survives the switch; the new org_id is a server claim, not a param — §24.8)
```

Three things this diagram makes concrete that the prose alone cannot:

- **The refresh is reactive, not just scheduled.** A `401` on any call can trigger it; the single-flight guard means ten concurrent `401`s produce *one* refresh, and the other nine await its result and retry. This is why a token expiring mid-session never produces a visible error.
- **The BFF is on the refresh path and nowhere else.** Only `/auth/refresh` and `/auth/logout` traverse the BFF, because only they touch the httpOnly cookie the browser sends automatically (§24.5). Every other call is a direct browser→API bearer call.
- **Org switch is a hard reset by design.** The new token is the *only* thing that changes tenant; the client throws away all cached org-A data rather than trying to reconcile it, because reconciliation is exactly the class of bug that leaks one tenant's data into another's view (§9.9, §24.8).

---

### 29.3 The Core Value Path — Blueprint → Generate → Stream → Review → Approve → Export

This is the journey the whole product exists to serve, and the one that exercises the most subsystems at once: form architecture (§15), async-job kickoff (§10, §17), live SSE progress *and* streamed question-ready events (§11), the paper builder's optimistic-but-safe editing model (§13), the separation-of-duties approval rule (BR-6, §7), and finally another async job for export. It is drawn as one continuous flow, split into four phases so each phase's failure modes stay legible.

**Phase A — Blueprint to kickoff.** The teacher composes a blueprint and asks the pipeline to fill it. The response is a `202`, not the paper — generation is a long-running server job, and the client's job from here is to *watch*, not to *poll a result*.

```
Phase A — KICKOFF
User      UI(builder)     Query        Fetch          API
 | define blueprint         |            |              |
 |----------------------->  |            |              |
 |  click "Generate"        |            |              |
 |----------------------->  |            |              |
 |         |-- validate blueprint (Zod, §16) --|        |
 |         |  [invalid → inline errors, nothing sent — work preserved]
 |         |-- POST /papers/generate {blueprint} ------>|
 |         |            |            |-- enqueue job, create paper(status=generating)
 |         |            |<----------- 202 {paperId, jobId} ----------|
 |         |-- open SSE subscription for paperId ------>|   (§11.4)
 |<- "Generating…" progress view (paperId known) -------|
```

**Phase B — Live generation over SSE.** The server drives; the client renders whatever arrives. Progress ticks update a bar; `paper.question.ready` events stream individual questions into the builder *as they are assembled*, so the teacher sees the paper fill in rather than waiting for a monolithic "done." The full SSE-consumption mechanics (reconnect, catch-up, PATCH-vs-invalidate) are §11.6–§11.9; here they are assumed.

```
Phase B — STREAMING GENERATION            (server→client; UI never polls)
API/worker           SSE            UI(builder)        Query
 | retrieval + AI gap-fill running   |                  |
 |-- paper.generation.progress {pct:20} -->|            |
 |                     |------------->|-- update bar     |
 |-- paper.question.ready {qId,payload} -->|            |
 |                     |------------->|-- append question card (streamed in)
 |-- paper.generation.progress {pct:70} -->|            |
 |                     |------------->|-- update bar     |
 |-- paper.generated {paperId, version:1} ->|           |
 |                     |------------->|-- invalidate [org,'papers',paperId] (§11.8)
 |                     |              |-- refetch canonical paper -->|
 |                     |              |<-------------- 200 {paper v1} |
 |<- builder shows complete paper, editable ------------|

  Failure branch:
 |-- paper.generation.failed {ehRef, requestId} ------->|
 |                     |------------->|-- surface EH-mapped message (§25.3), keep blueprint,
 |                                        offer "Retry generation" (blueprint never lost — §25.1)
```

**Phase C — Review and edit in the builder.** The teacher revises. Edits use the paper-builder store (§9, §13); safe reversible actions are optimistic, everything canonical is confirmed by the server. A concurrent edit from another author surfaces as a `409` and routes into the recovery flow of §29.6 rather than silently clobbering.

```
Phase C — REVIEW / EDIT
User      UI(builder)      Query          Fetch        API
 | edit a question / reorder |             |            |
 |----------------------->   |             |            |
 |         |-- optimistic apply (safe action — §13.10) |
 |         |-- PATCH /papers/{id}/questions/... (If-Match: version) ->|
 |         |             |             |<-- 200 {new version} -------|
 |         |-- reconcile store to server version -------|
 |<- edit confirmed --------|             |            |
   (on 409 → §29.6 conflict recovery; optimistic edit is rolled back, not lost)
```

**Phase D — Approval (BR-6) then export.** Approval enforces **separation of duties**: the author of a paper may not be its sole approver (BR-6, §7). The client *projects* this rule so the "Approve" affordance is hidden from the author (§7's "hide by role, disable by state"), but the server is the enforcer — an author who forges the call is rejected by `require_roles` or the business rule with a `403`/`409`. Export is then another async job with its own progress and `export.ready` terminal event carrying a signed download URL (§17, §11).

```
Phase D — APPROVE (BR-6) then EXPORT
Author    UI          API                 Approver   UI'         API
 | (author sees NO Approve button — projected per BR-6, §7)
 | request approval        |               |          |           |
 |----------------------->|-- POST /papers/{id}/approval-request ->|
 |         |<------------- 200 {status: pending} -----|            |
 |                        |-- notification.created {approval.requested} --> (§23.4)
 |                        |               |          |  approver notified
 |                        |               | open paper |           |
 |                        |               |--------->|-- GET /papers/{id} ->|
 |                        |               |          |<-- 200 {paper, canApprove:true} --|
 |                        |               | click Approve         |
 |                        |               |--------->|-- POST /papers/{id}/approve ------>|
 |                        |               |          |   [server re-checks BR-6: approver ≠ author]
 |                        |               |          |<-- 200 {status: approved, version} |
 |                        |<- paper.approval.changed {approved} -- (§11, §23.5) ---------|
 |  author's view updates live to "Approved" (SSE-driven, no refresh)

EXPORT (async job, §17)
User      UI            Fetch         API            SSE          Store(obj)
 | click "Export PDF"     |            |              |             |
 |--------------------->  |-- POST /papers/{id}/export {fmt:pdf} ->|             |
 |         |              |<---------- 202 {jobId} ---|             |
 |         |-- subscribe export progress ------------>|            |
 |         |              |            |-- render + upload artifact ------------>|
 |         |              |            |-- export.progress {pct} -->|            |
 |         |<-------------------------- (bar updates via SSE) ------|            |
 |         |              |            |-- export.ready {signedUrl, ttl:1h} ---->|  (§17.15)
 |         |<- "Download ready" (capability URL, expires in 1h) ---|            |
 | click download          |----------------------------------------------------->| GET signedUrl
 |<------------------------------------------------------------- file bytes -----|
```

What the composed flow demonstrates that the per-mechanism diagrams cannot:

- **The client never owns "is this paper done."** Phases B, D-approve, and D-export are all server-driven over SSE. The UI's role is to render authoritative events and invalidate its cache on the terminal event, never to decide completion itself (§11.8).
- **BR-6 is enforced twice, by design.** Once as a *projection* so the author is never shown an affordance they cannot use, and once as *enforcement* on the server that rejects a forged call. The client projection is UX, not security (§24.1); removing it would be a usability bug, not a vulnerability.
- **Every failure branch keeps the teacher's work.** A failed generation keeps the blueprint; a `409` on edit rolls back the optimistic change without discarding intent; a failed export leaves the approved paper untouched. This is §25.1 made concrete across a five-subsystem flow.
- **Signed URLs are the only data-path exit.** The exported file never streams through the API; the browser fetches it directly from object storage with a short-lived capability URL (§17.15, §24.9).

---

### 29.4 The Signed-URL Upload Handshake — Textbook Ingestion Entry

Uploads never flow through the API (§17.1). This three-step handshake is the reason: the client asks the API to *mint a capability*, uploads the bytes *directly to object storage*, then tells the API the bytes have landed. The API stays out of the data path entirely, so a 400 MB textbook scan never touches an application server. The focused version of this diagram is §17.6; here it is shown through to the downstream ingestion job that the upload triggers, so the reader sees where the SSE-driven ingestion flow (§11, [05_Textbook_Ingestion.md](05_Textbook_Ingestion.md)) picks up.

```
UPLOAD HANDSHAKE (three steps: request → PUT → complete)
User     UI          Fetch        API              Store(obj)      SSE
 | choose file        |            |                 |             |
 |----------------->  |-- validate type/size client-side (§17.9) --|
 | STEP 1: request a signed PUT URL |                 |             |
 |         |-- POST /uploads/sign {name,size,type} -->|             |
 |         |            |            |-- authz + quota check, mint PUT URL (ttl 15m — §17.13)
 |         |            |<----------- 200 {uploadUrl, objectKey} --|
 | STEP 2: PUT bytes directly to storage (API not in path)         |
 |         |-- PUT bytes ------------------------------------------>|
 |         |            |            |     (progress from XHR/fetch upload events — §17.11)
 |         |<------------------------------------------ 200 OK -----|
 | STEP 3: tell the API the object is complete                     |
 |         |-- POST /uploads/complete {objectKey} --->|             |
 |         |            |            |-- verify object exists, enqueue ingestion job (202)
 |         |            |<----------- 202 {textbookId, jobId} -----|
 |         |-- subscribe textbook.* for textbookId --------------->|
 |<- "Processing…" (ingestion now server-driven) ------|           |

INGESTION (server-driven, same progress pattern as generation — §11)
 |-- textbook.progress {pct} ----------------------------------->|-- bar
 |-- textbook.parsed {textbookId} ------------------------------>|-- invalidate + refetch
 |   (or) textbook.failed {ehRef} ------------------------------>|-- EH-mapped message, file retained
```

The handshake's shape is a security decision as much as a scalability one: the signed URL is a **bearer capability scoped to one object and expiring in 15 minutes** (§17.13, §24.9). It cannot be used to read or write anything else, and it is useless once it expires — so even if it leaked into a log, the blast radius is one object for one short window.

### 29.5 Notification Live-Delivery and Mark-Read

Notifications have two systems (§23.1): the transient toast layer (client-only, ephemeral) and the durable notification centre (server-owned, survives reload). This diagram covers the durable path — live delivery over SSE, the dedicated unread-count endpoint, and the optimistic mark-read. The focused delivery sequence is §23.5; this adds the read-side round-trip and the cross-tab broadcast so the full lifecycle is visible.

```
LIVE DELIVERY + MARK-READ
API           SSE          UI(leader tab)   Query            Other tabs
 | server-side event occurs |                |                 |
 |-- notification.created {id,type,...} ---->|                 |
 |              |----------->|-- dedup by id (§23.5)           |
 |              |            |-- prepend to centre cache        |
 |              |            |-- invalidate [org,'notifications','unreadCount'] (§23.6)
 |              |            |-- broadcast count to other tabs (leader owns SSE — §23.10) --> updates badge
 |              |            |-- show transient toast if policy allows (§23.3)
User opens notification centre |                |                 |
 |----------------------------->|-- GET /notifications?cursor (§23.7) -->|
 |              |            |<------------------ 200 {items, nextCursor} --|
User clicks one (mark read)    |                |                 |
 |----------------------------->|-- optimistic: mark read, decrement count (§23.7)
 |              |            |-- POST /notifications/{id}/read ------------>|
 |              |            |<----------------------------- 200 {ok} -----|
 |              |            |   (on failure → roll back the one item, count restored — §23.11)
"Mark all read"                |                |                 |
 |----------------------------->|-- POST /notifications/read-all (server op over whole set — §23.7)
 |              |            |<----------------------------- 200 {ok} -----|
 |              |            |-- invalidate centre + unreadCount (no optimistic count guess over unbounded set)
```

Two design points the diagram surfaces:

- **Unread count is its own lightweight endpoint.** The badge does not compute a count by walking the notification list — it reads a dedicated `unreadCount` query key that the server serves cheaply and that live events invalidate (§23.6). This keeps the badge correct without paging the whole notification history.
- **"Mark all read" is a server operation, not an optimistic sweep.** Marking one item is optimistic because its scope is bounded and reversible; marking *all* read spans a potentially unbounded set, so the client defers to the server and re-reads rather than guessing (§23.7). This is the optimistic-only-when-safe rule (§10, §13.10) applied to the notification surface.

### 29.6 Error / Recovery — Optimistic-Lock `409` Conflict

This is the failure path that most tests the "no failure discards the user's work" invariant (§25.1). Two authors edit the same paper; the second write arrives against a stale version and the server returns `409` (optimistic-lock conflict, §10, §25.2). The client must roll back its optimistic change, show the divergence honestly, and — critically — **keep the user's attempted edit** so it can be reapplied, never silently thrown away.

```
409 OPTIMISTIC-LOCK CONFLICT + RECOVERY
User(B)   UI(builder)      Query          Fetch        API
 | (paper at version 5 in B's builder; author A has since saved version 6)
 | edit question, save     |               |            |
 |----------------------->|-- optimistic apply locally (still thinks v5)
 |         |-- PATCH /papers/{id}/questions/... (If-Match: 5) --->|
 |         |               |               |   [server: current is v6 ≠ 5]
 |         |               |<------------- 409 {code:'version_conflict', ehRef} --|
 |         |-- roll back the optimistic edit (store restored to last-confirmed)
 |         |-- STASH B's attempted change (not discarded — §25.1)
 |         |-- fetch canonical latest -------------------------->|
 |         |               |<------------------ 200 {paper v6} --|
 |<- conflict surfaced: "This paper changed since you opened it." |
 |   options: [Review changes] [Reapply my edit] [Discard my edit]
 |
 | choose "Reapply my edit"  |               |            |
 |----------------------->|-- re-apply stashed change onto v6 (optimistic again)
 |         |-- PATCH ... (If-Match: 6) ------------------------->|
 |         |               |<------------------ 200 {v7} -------|
 |         |-- reconcile to v7 -----------------------------------|
 |<- edit confirmed on top of latest ---------|            |
```

The recovery flow embodies three of the document's load-bearing rules at once:

- **Optimistic, but reversible.** The edit was applied optimistically for responsiveness (§13.10), but the store retained the last server-confirmed state, so the `409` rollback is exact — the UI returns to a known-good version rather than a guess (§10).
- **Conflict is surfaced, never resolved silently.** The client does not attempt an automatic three-way merge of paper content; it tells the user the truth ("this changed since you opened it") and hands them the decision (§25.8). A silent merge is exactly the kind of "helpful" behaviour that corrupts a paper.
- **The user's work is stashed, not lost.** "Reapply my edit" exists because the attempted change was preserved through the conflict. This is §25.1 at its most concrete: even the failure of a write keeps the intent that produced it.

### 29.7 What These Diagrams Establish Together

Read as a set, the six journeys close the loop on the architecture's central claims:

| Journey | Load-bearing rule it proves in motion |
|---|---|
| §29.2 Auth lifecycle | The client holds a short-lived token in memory; refresh is single-flight and invisible; org switch is a hard reset (§6, §9.9, §24.8) |
| §29.3 Core value path | Long-running work is server-driven over SSE; the client renders authoritative events and never owns completion (§11) |
| §29.3 (BR-6) | Business rules are projected for UX and enforced on the server; the client projection is not the boundary (§7, §24.1) |
| §29.4 Upload handshake | The API is never in the data path; signed URLs are short-lived scoped capabilities (§17, §24.9) |
| §29.5 Notifications | Server-owned state stays server-owned; optimistic only when bounded and reversible (§9, §23) |
| §29.6 `409` recovery | No failure discards the user's work; conflicts are surfaced, not silently merged (§25.1, §25.8) |

Every one of these is a rule stated abstractly earlier in the document. The value of the sequence diagrams is that they show the rules *composing* — an engineer who understands these six flows understands why the rest of the document is shaped the way it is.

---

## 30. Related Documents

This document is one member of a set. It deliberately stops at the frontend's edge: where it describes the token model, the guard chain, the envelope shape, the SSE taxonomy, or the ingestion pipeline, it is *describing the contract it consumes*, not defining it. The definitions of record live in the sibling documents below. When this document and a sibling disagree, the sibling that owns the concept wins, and this document should be corrected to match.

### 30.1 The Document Set

| # | Document | Owns (definition of record for…) | This document leans on it for… |
|---|---|---|---|
| 01 | [01_Product_Requirements.md](01_Product_Requirements.md) | Requirement IDs — `FR-` (functional), `BR-` (business rules, e.g. BR-6 separation of duties), `AI-`, `EH-` (EH-1..EH-18 error handling), `NT-` (notifications), `CN-` (constraints); roles teacher / coaching_admin / super_admin | Every requirement cross-reference; BR-6 (§29.3, §7); the EH catalogue (§25.3) |
| 02 | [02_System_Architecture.md](02_System_Architecture.md) | The service topology, tenancy model, and where the frontend sits in the whole | The system context the frontend is one component of (§1) |
| 03 | [03_Database_Design.md](03_Database_Design.md) | Schema, `org_id` tenancy columns, PostgreSQL RLS | What "tenant-scoped" and RLS mean behind the API (§7, §24.8) |
| 04 | [04_ER_Diagram.drawio](04_ER_Diagram.drawio) | The entity-relationship diagram | The domain entities the UI renders (papers, blueprints, question bank, templates) |
| 05 | [05_Textbook_Ingestion.md](05_Textbook_Ingestion.md) | The ingestion/OCR pipeline and its `textbook.*` events | The downstream flow the upload handshake feeds (§17, §29.4) |
| 06 | [06_AI_Architecture.md](06_AI_Architecture.md) | The generation pipeline (retrieval → AI gap-fill → assembly), prompt versions | What the paper-generation SSE stream reflects (§13, §29.3) |
| 07 | [07_Backend_Architecture.md](07_Backend_Architecture.md) | **The guard chain, JWT/refresh model, response envelopes, HTTP status conventions, SSE event taxonomy, signed-URL brokering, list conventions** | This is the primary contract source — §6, §7, §10, §11, §17 all consume its definitions |
| 09 | [09_Deployment.md](09_Deployment.md) | Build/deploy pipeline, environments, CSP delivery, release identifiers | CSP (§24.6), release tagging in monitoring (§26.2), CI budgets (§27.9) |

### 30.2 Supporting Documents

| Document | Role |
|---|---|
| [ADR/](ADR/) | Architecture Decision Records — the *why* behind cross-cutting choices; where a Design Decisions row in this document needs its full history, the ADR is the long form |
| [Blueprint_AI_Question_Paper_Generator.md](Blueprint_AI_Question_Paper_Generator.md) | The product blueprint / north-star framing the whole document set serves |
| [Production_Grade_Claude_Prompt_AI_Question_Paper_Generator.md](Production_Grade_Claude_Prompt_AI_Question_Paper_Generator.md) | The production prompt specification behind the AI generation behaviour (§06 owns the architecture; this owns the prompt) |

### 30.3 How This Document References the Others

Two conventions are used consistently throughout, and are worth stating once here so the reader can rely on them:

- **`§N` (and `§N.M`) always means a section of *this* document.** An internal cross-reference never points outside the frontend architecture doc.
- **A markdown link like [07_Backend_Architecture.md](07_Backend_Architecture.md) always means a *sibling* document.** External references are always links, never bare `§` numbers, so the two can never be confused.

Requirement IDs (`FR-`, `BR-`, `AI-`, `EH-`, `NT-`, `CN-`) are owned by [01_Product_Requirements.md](01_Product_Requirements.md) and are cited inline wherever a frontend behaviour exists to satisfy one — most prominently BR-6 (separation of duties, §29.3) and the EH-1..EH-18 catalogue that the error-handling section maps to (§25.3).

### 30.4 Reading Order for a New Engineer

For someone joining the frontend team, the fastest path to productive context is:

1. **[01_Product_Requirements.md](01_Product_Requirements.md)** — what the product is and who the three roles are.
2. **[02_System_Architecture.md](02_System_Architecture.md)** — where the frontend sits in the whole.
3. **[07_Backend_Architecture.md](07_Backend_Architecture.md)** — the contract this document consumes; read this *before* the deep frontend sections, because §6, §7, §10, §11, and §17 all assume it.
4. **This document, §1–§13** — principles, structure, and the core mechanisms up through the paper builder.
5. **This document, §29** — the sequence diagrams, which tie every mechanism together into the journeys that matter.
6. **Everything else in this document, as needed** — the remaining sections are reference material best read against a specific task.

That order front-loads the contract and the shape of the system, then the mechanisms, then the composed flows — which is the same order in which the design decisions themselves were made.

---

*End of document.*
