# 09 Deployment Architecture

## 1. Overview

### Purpose

This document defines the complete deployment architecture for the AI-powered Question Paper Generation SaaS platform. It specifies how every component — frontend, backend, workers, databases, caches, object storage, AI providers, and supporting infrastructure — is built, packaged, deployed, scaled, monitored, secured, backed up, and recovered in production.

This is an operational engineering document. It does not describe what the platform does (that belongs in `01_Product_Requirements.md` and `02_System_Architecture.md`). It describes how the platform runs. Every section answers a concrete deployment question: where does this component live, how does it start, how does it scale, what happens when it fails, and who gets paged.

This document should be read alongside `02_System_Architecture.md` (logical modules, data flows), `03_Database_Design.md` (schema, RLS, indexes), `06_AI_Architecture.md` (provider abstraction, embedding pipeline), `07_Backend_Architecture.md` (FastAPI modules, Celery wiring), and `08_Frontend_Architecture.md` (Next.js rendering strategy).

### Goals

| Goal | Measure |
|------|---------|
| High availability | 99.9% uptime for API and frontend; no single point of failure in production |
| Horizontal scalability | Every stateless component scales to N replicas with zero code changes |
| Secure multi-tenancy | Tenant data isolation enforced at network, application, and database layers |
| Automated deployment | Zero-touch promotion from merge to production via CI/CD |
| Observable operations | Every request traceable end-to-end; every failure alerted within 60 seconds |
| Disaster recovery | RPO ≤ 1 hour, RTO ≤ 4 hours for full platform recovery |
| Cost efficiency | Right-sized resources with auto-scaling; no idle over-provisioning |

### Deployment Philosophy

The platform follows a **progressive deployment model**: Docker Compose for development, **ECS Fargate
for MVP and Beta production**, and Kubernetes as the documented scale target adopted on evidence rather
than on schedule.

### Deployment target by stage (ADR-013)

| Stage | Target | Rationale |
|---|---|---|
| Development | Docker Compose | One command, real Postgres/Redis/MinIO, per-service log streams (§6.5) |
| CI | Docker Compose / Testcontainers | Same images, ephemeral data |
| **MVP → Beta production** | **AWS ECS Fargate** — one `api` service, one service per worker type | Two application services and one managed database do not justify a Kubernetes control plane. Fargate gives autoscaling, rolling deploys, health-gated traffic, and per-service log streams with no cluster to operate. |
| **GA → scale** | **Kubernetes** (this document's §7) | Adopted when a measured trigger fires, not before |

**Adoption triggers for Kubernetes** — any one is sufficient:

- More than roughly six independently deployable services, where per-service scheduling policy starts
  to matter more than the control-plane cost.
- A need for scheduling primitives Fargate does not express: node affinity, GPU node pools (a likely
  driver if OCR moves to a local vision model), or fine-grained spot/on-demand mixing.
- Multi-region active-active, where a single orchestration API across regions is worth its overhead.

**The Kubernetes manifests in §7 are written and validated in CI from day one**, and are deliberately
not applied to a production cluster until a trigger fires. Writing them early keeps the migration a
configuration exercise; adopting them early would buy a control plane, a cluster upgrade cadence, and a
node-pool management burden that a two-service system does not repay. Everything in §7 is therefore
**the scale target**, not the MVP topology — read it that way.

**Core tenets:**

1. **Containers everywhere.** Every component ships as a Docker image. Local, CI, staging, and production all run the same images. "Works on my machine" is eliminated.

2. **Infrastructure as configuration.** All deployment topology is defined in version-controlled manifests — Docker Compose files for development, Kubernetes YAML for production. No snowflake servers.

3. **Stateless application tier.** Frontend, backend, and workers carry no local state. Any instance can be killed and replaced without data loss. State lives in PostgreSQL, Redis, and object storage.

4. **Twelve-Factor compliance.** Configuration via environment variables. Logs to stdout. Backing services as attached resources. Disposable processes. Dev/prod parity.

5. **Blast-radius containment.** Failures in one worker type do not cascade to the API. Database connection exhaustion in the backend does not affect the frontend. Namespace and network isolation enforce these boundaries.

6. **Ship small, ship often.** The CI/CD pipeline supports multiple daily deployments. Rolling updates ensure zero-downtime releases. Rollback is a single command.

### Infrastructure Overview

The production platform runs as a set of containerized services orchestrated by Kubernetes, fronted by a CDN and load balancer, backed by managed PostgreSQL, Redis, and S3-compatible object storage, and connected to external AI providers over HTTPS.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PRODUCTION INFRASTRUCTURE                         │
│                                                                             │
│  ┌──────────┐   ┌───────────────┐   ┌────────────────────────────────────┐  │
│  │   CDN    │──▶│ Load Balancer │──▶│         Kubernetes Cluster         │  │
│  └──────────┘   └───────────────┘   │                                    │  │
│                                     │  ┌─────────┐  ┌─────────────────┐  │  │
│                                     │  │ Nginx   │  │    Frontend     │  │  │
│                                     │  │ Ingress │──│    (Next.js)    │  │  │
│                                     │  │         │  │    3 replicas   │  │  │
│                                     │  │         │  ├─────────────────┤  │  │
│                                     │  │         │──│    Backend      │  │  │
│                                     │  │         │  │   (FastAPI)     │  │  │
│                                     │  │         │  │    3 replicas   │  │  │
│                                     │  └─────────┘  └────────┬────────┘  │  │
│                                     │                        │           │  │
│                                     │  ┌─────────────────────┼────────┐  │  │
│                                     │  │      Workers        │        │  │  │
│                                     │  │  ┌─────┐ ┌─────┐ ┌─┴───┐   │  │  │
│                                     │  │  │ OCR │ │ Gen │ │ Exp │   │  │  │
│                                     │  │  └─────┘ └─────┘ └─────┘   │  │  │
│                                     │  └─────────────────────────────┘  │  │
│                                     └──────────────┬─────────────────────┘  │
│                                                    │                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────┴──┐  ┌──────────────┐  │
│  │  PostgreSQL  │  │    Redis     │  │ Object Storage │  │  Monitoring  │  │
│  │  + pgvector  │  │  Cache/Queue │  │  (S3-compat)   │  │  + Logging   │  │
│  │  Primary +   │  │  Primary +   │  │                │  │              │  │
│  │  Replica     │  │  Replica     │  │                │  │              │  │
│  └──────────────┘  └──────────────┘  └────────────────┘  └──────────────┘  │
│                                                                             │
│                          ┌──────────────────────┐                           │
│                          │  External AI APIs    │                           │
│                          │  LLM + Embedding     │                           │
│                          └──────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Relationship with System Architecture

`02_System_Architecture.md` defines the logical modules and their interactions — Auth, Curriculum, Questions, Retrieval, AI Service, Papers, Workers. This document defines where those modules physically run, how they are packaged, how they communicate over the network, and how they are managed operationally.

| System Architecture Concept | Deployment Architecture Mapping |
|------------------------------|----------------------------------|
| FastAPI API (all modules) | `api` service — an ECS Fargate service at MVP, a Deployment at K8s scale |
| Next.js frontend | `web` service — separate image, separate process, separate log stream |
| OCR Worker | `worker-ocr` Deployment, HPA-scaled |
| Generation Worker | `worker-generation` Deployment, HPA-scaled |
| Export Worker | `worker-export` Deployment, HPA-scaled |
| PostgreSQL (+ pg_trgm, citext; pgvector only when adopted) | Managed database, primary + read replica |
| Redis (cache + queues) | Managed Redis or StatefulSet, primary + replica |
| Object Storage | S3-compatible managed service, external to cluster |
| AI Service Layer | Outbound HTTPS to the Gemini API (allowlisted egress) |
| Domain Events (pub/sub) | Redis pub/sub channels, private network only |

---

## 2. Deployment Objectives

### High Availability

The platform targets **99.9% availability** (≤ 8.76 hours unplanned downtime per year). This is achieved through:

- **No single points of failure.** Every stateless component runs a minimum of 3 replicas across availability zones. PostgreSQL runs as primary + synchronous replica. Redis runs as primary + replica with automatic failover.
- **Health-check-driven routing.** The load balancer and Kubernetes remove unhealthy instances from rotation within seconds. Liveness probes restart crashed containers. Readiness probes prevent traffic to instances still warming up.
- **Rolling deployments.** New versions roll out pod-by-pod. If a new pod fails its health check, the rollout halts and rolls back automatically. Zero-downtime deployments are the norm, not the exception.
- **Graceful degradation.** If an AI provider is unreachable, generation requests queue for retry rather than failing immediately. If Redis is temporarily unavailable, the backend serves requests from the database with degraded cache performance.

### Scalability

- **Horizontal scaling** for all stateless components (frontend, backend, workers). Adding replicas requires no code changes and no downtime.
- **Vertical scaling** for stateful components (PostgreSQL, Redis) through managed service tier upgrades.
- **Independent scaling** for each worker type. OCR-heavy periods (start of term, bulk textbook uploads) scale the OCR worker independently of the Generation worker.
- **Auto-scaling** based on CPU, memory, and custom queue-depth metrics. Kubernetes Horizontal Pod Autoscaler manages replica counts.

### Security

- **Defense in depth.** TLS terminates at the load balancer/Ingress. Internal cluster traffic uses mTLS or private networking. Secrets are never stored in code or container images.
- **Tenant isolation.** PostgreSQL Row-Level Security enforces data boundaries at the database layer. Application-layer tenant guards provide the first line of defense. Network policies prevent cross-namespace communication.
- **Least privilege.** Service accounts carry only the permissions their workloads require. Container images run as non-root. No component has cluster-admin access.
- **Supply chain security.** Docker images are scanned for CVEs in CI. Dependencies are audited on every build. Only signed images from the private registry are deployed.

### Reliability

- **Idempotent deployments.** Running the same deployment twice produces the same result. Database migrations are forward-only and idempotent.
- **Retry and dead-letter patterns.** Failed worker jobs retry with exponential backoff. After max retries, jobs move to a dead-letter queue for manual investigation — they never silently disappear.
- **Circuit breakers.** External dependencies (AI providers, embedding services) are wrapped in circuit breakers that trip after sustained failures, preventing cascade overload.

### Maintainability

- **Infrastructure as code.** Every deployment artifact is version-controlled. No manual cluster configuration. New environments are created from the same manifests.
- **Operational runbooks.** Standard procedures for deployment, rollback, scaling, restart, migration, and incident response are documented and tested.
- **Automated maintenance.** Certificate renewal, secret rotation, dependency updates, and database vacuuming run on schedules, not on human memory.

### Cost Efficiency

- **Right-sized resources.** Resource requests and limits are set based on observed usage, not guesses. Over-provisioned components are resized downward.
- **Auto-scaling to zero during low traffic.** Worker replicas scale down to minimum counts during off-peak hours (nights, weekends). They scale up automatically when queue depth increases.
- **Storage lifecycle policies.** Temporary processing artifacts are automatically deleted after configurable retention periods. Long-term backups use cold storage tiers.
- **Spot/preemptible instances.** Worker nodes that tolerate interruption run on spot instances for significant cost savings.

### Observability

- **Three pillars.** Metrics (Prometheus), logs (Loki/ELK), and traces (OpenTelemetry/Jaeger) provide full operational visibility.
- **Correlation IDs.** Every request carries a unique `x-request-id` that propagates through the frontend, backend, workers, and database logs. A single ID traces a request from browser to database and back.
- **Alerting.** Critical alerts (API error rate > 5%, worker queue depth > 1000, database connection pool exhaustion, disk > 85%) page the on-call engineer within 60 seconds.

### Disaster Recovery

- **RPO ≤ 1 hour.** Continuous WAL archiving for PostgreSQL ensures at most 1 hour of data loss in a catastrophic failure.
- **RTO ≤ 4 hours.** Full platform recovery from backup — including database restore, Redis re-warm, and service redeployment — completes within 4 hours.
- **Regular drills.** Disaster recovery procedures are tested quarterly. Backup restores are verified weekly. Chaos engineering exercises run monthly.

---

## 3. Deployment Architecture

### Component Descriptions

**Browser.** The end-user's browser. Teachers, Coaching Admins, and Super Admins interact with the platform through a modern web browser. The browser communicates exclusively with the CDN and load balancer over HTTPS. It never contacts backend services directly.

**CDN (Content Delivery Network).** A globally distributed edge cache (e.g., Cloudflare, AWS CloudFront) that serves static assets — JavaScript bundles, CSS, fonts, and images — from edge locations closest to the user. The CDN reduces latency for asset loading and offloads bandwidth from the origin servers. Cache-control headers and content hashing ensure correct cache invalidation on deployments.

**Load Balancer.** A Layer 7 (HTTP/HTTPS) load balancer that terminates TLS and distributes incoming requests across Kubernetes Ingress controllers. It performs health checks against backend targets and removes unhealthy nodes from rotation. The load balancer handles SSL certificate management, HTTP/2, and WebSocket upgrades for real-time progress streams.

**Nginx Ingress Controller.** Runs inside the Kubernetes cluster as the entry point for all HTTP traffic. Nginx routes requests based on host and path:
- `app.example.com/*` → Frontend (Next.js) Service
- `app.example.com/api/*` → Backend (FastAPI) Service
- `app.example.com/api/health` → Health check endpoint

Nginx also handles rate limiting, request size limits (for file uploads), gzip compression, security headers, and CORS enforcement.

**Frontend (Next.js).** The server-side rendered and client-side hydrated web application. Runs as a stateless Node.js process. Each replica handles SSR requests, serves the React application shell, and proxies API calls to the backend service. Replicas are identical and interchangeable.

**Backend (FastAPI API).** The modular monolith application server, a Python/uvicorn process. Handles all REST API requests: authentication, authorization, tenant resolution, CRUD operations, job enqueueing, and real-time event streaming (SSE/WebSocket). Each replica connects to PostgreSQL (via connection pool), Redis (for cache and queue operations), and object storage (for signed URL generation). Replicas are stateless and horizontally scalable.

**Redis.** Serves three roles:
1. **Cache** — Session data, curriculum lookups, rate-limit counters, and frequently accessed query results.
2. **Queue** — Celery broker and result backend for OCR, generation, export, notification, and embedding tasks.
3. **Pub/Sub** — Real-time progress events from workers to the API, which forwards them to the frontend via SSE/WebSocket.

**Workers.** Stateless, long-running Python (Celery) processes that consume tasks from Redis-backed queues. They run as their own services with their own log streams, entirely separate from the API and the frontend. Each worker type runs as an independent Kubernetes Deployment with its own scaling policy:
- **OCR Worker** — Processes uploaded textbook files: PDF-to-image conversion, OCR text extraction, structural parsing. CPU-intensive, benefits from horizontal scaling during bulk upload periods.
- **Generation Worker** — Orchestrates the retrieval-augmented generation pipeline: candidate selection, AI generation for unfilled slots, validation, and question bank insertion. I/O-bound (waiting on LLM API calls), scales based on queue depth.
- **Export Worker** — Renders finalized papers to PDF and DOCX formats. CPU and memory intensive during rendering, I/O-bound during upload to object storage.

**PostgreSQL.** The system of record. Stores all transactional data: organizations, users, memberships, curriculum, questions, papers, notifications, audit logs, billing records. Extensions at MVP are `pg_trgm`, `citext`, and `pgcrypto`. **pgvector is not installed at MVP** — it is added only when the eval suite records a paraphrase-recall gap (ADR-007), at which point it stores and indexes embedding vectors for semantic search. Runs as a primary instance with one synchronous replica for high availability and one asynchronous replica for read scaling.

**Object Storage (S3-compatible).** Stores binary assets: uploaded textbook files (PDF, DOCX, images), extracted page images, rendered paper exports (PDF, DOCX), and template assets. Accessed via signed URLs for secure, time-limited direct browser uploads and downloads. The backend never proxies large file transfers — it generates signed URLs and the browser communicates directly with storage.

**AI Provider.** The **Gemini API**, accessed over HTTPS. The AI Service Layer (defined in `06_AI_Architecture.md`) abstracts it behind the `LlmProvider` adapter interface; Gemini is the only implemented adapter (ADR-008). The deployment architecture treats these as external dependencies with circuit breakers, retries, timeouts, and rate limiting.

**Monitoring Stack.** Prometheus for metrics collection and alerting. Grafana for dashboards. Loki or ELK for centralized log aggregation. OpenTelemetry or Jaeger for distributed tracing. Alertmanager for routing alerts to PagerDuty, Slack, or email.

### Full Deployment Diagram

```
                              ┌──────────────────┐
                              │     Teacher /     │
                              │  Coaching Admin / │
                              │   Super Admin     │
                              └────────┬─────────┘
                                       │ HTTPS
                                       ▼
                              ┌──────────────────┐
                              │       CDN         │
                              │  (Static Assets)  │
                              └────────┬─────────┘
                                       │ HTTPS (cache miss)
                                       ▼
                              ┌──────────────────┐
                              │  Load Balancer    │
                              │  (TLS Termination)│
                              └────────┬─────────┘
                                       │ HTTP/2
                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        KUBERNETES CLUSTER                                │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │                    INGRESS NAMESPACE                              │    │
│  │  ┌────────────────────────────────────────────────────────────┐  │    │
│  │  │               Nginx Ingress Controller                     │  │    │
│  │  │  • Path routing    • Rate limiting    • Security headers   │  │    │
│  │  │  • Gzip            • CORS             • Request size limit │  │    │
│  │  └───────────┬────────────────────────────┬───────────────────┘  │    │
│  └──────────────┼────────────────────────────┼──────────────────────┘    │
│                 │ /*, /api/health             │ /api/*                    │
│                 ▼                             ▼                           │
│  ┌──────────────────────┐     ┌──────────────────────────────────────┐   │
│  │   APP NAMESPACE       │     │          APP NAMESPACE               │   │
│  │                       │     │                                      │   │
│  │  ┌─────────────────┐ │     │  ┌────────────────────────────────┐  │   │
│  │  │   Frontend SVC  │ │     │  │        Backend SVC             │  │   │
│  │  │   (ClusterIP)   │ │     │  │        (ClusterIP)             │  │   │
│  │  └────────┬────────┘ │     │  └───────────┬────────────────────┘  │   │
│  │           │          │     │              │                       │   │
│  │  ┌────────┴────────┐ │     │  ┌───────────┴────────────────────┐  │   │
│  │  │  Next.js Pods   │ │     │  │      FastAPI Pods              │  │   │
│  │  │  ┌────┐ ┌────┐  │ │     │  │  ┌────┐ ┌────┐ ┌────┐        │  │   │
│  │  │  │ P1 │ │ P2 │  │ │     │  │  │ P1 │ │ P2 │ │ P3 │        │  │   │
│  │  │  └────┘ └────┘  │ │     │  │  └────┘ └────┘ └────┘        │  │   │
│  │  │  ┌────┐          │ │     │  └──────────────────────────────┘  │   │
│  │  │  │ P3 │          │ │     │                                      │   │
│  │  │  └────┘          │ │     │                                      │   │
│  │  └──────────────────┘ │     └──────────────────────────────────────┘   │
│  └───────────────────────┘                                               │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                     WORKERS NAMESPACE                              │   │
│  │                                                                    │   │
│  │  ┌──────────────┐   ┌──────────────────┐   ┌───────────────────┐  │   │
│  │  │  OCR Worker   │   │ Generation Worker│   │  Export Worker    │  │   │
│  │  │  Deployment   │   │  Deployment      │   │  Deployment      │  │   │
│  │  │  ┌────┐┌────┐│   │  ┌────┐ ┌────┐  │   │  ┌────┐ ┌────┐  │  │   │
│  │  │  │ P1 ││ P2 ││   │  │ P1 │ │ P2 │  │   │  │ P1 │ │ P2 │  │  │   │
│  │  │  └────┘└────┘│   │  └────┘ └────┘  │   │  └────┘ └────┘  │  │   │
│  │  └──────────────┘   └──────────────────┘   └───────────────────┘  │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                   DATA NAMESPACE                                   │   │
│  │                                                                    │   │
│  │  ┌──────────────────┐  ┌───────────────┐  ┌────────────────────┐  │   │
│  │  │   PostgreSQL     │  │    Redis       │  │                    │  │   │
│  │  │   ┌──────────┐   │  │  ┌──────────┐ │  │  Object Storage   │  │   │
│  │  │   │ Primary  │   │  │  │ Primary  │ │  │  (External S3)    │  │   │
│  │  │   └────┬─────┘   │  │  └────┬─────┘ │  │                    │  │   │
│  │  │        │ WAL      │  │       │       │  │                    │  │   │
│  │  │   ┌────▼─────┐   │  │  ┌────▼─────┐ │  │                    │  │   │
│  │  │   │ Replica  │   │  │  │ Replica  │ │  │                    │  │   │
│  │  │   └──────────┘   │  │  └──────────┘ │  │                    │  │   │
│  │  └──────────────────┘  └───────────────┘  └────────────────────┘  │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                MONITORING NAMESPACE                                 │   │
│  │  ┌────────────┐ ┌─────────┐ ┌──────┐ ┌────────────┐ ┌──────────┐ │   │
│  │  │ Prometheus │ │ Grafana │ │ Loki │ │ Jaeger     │ │AlertMgr  │ │   │
│  │  └────────────┘ └─────────┘ └──────┘ └────────────┘ └──────────┘ │   │
│  └───────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
                    │                       │
                    ▼                       ▼
        ┌──────────────────┐    ┌──────────────────────┐
        │  AI Providers    │    │  Backup Storage      │
        │  (LLM, Embed)   │    │  (Off-site, Encrypted)│
        └──────────────────┘    └──────────────────────┘
```

---

## 4. Infrastructure Components

### Component Details

| Component | Purpose | Responsibilities | Scaling Strategy | Failure Impact | Communication |
|-----------|---------|------------------|------------------|----------------|---------------|
| **CDN** | Edge-cache static assets, reduce origin load | Serve JS bundles, CSS, fonts, images; absorb DDoS at the edge | Globally distributed by design; scales with provider capacity | Fallback to origin; increased latency, higher origin load | HTTPS to load balancer on cache miss |
| **Load Balancer** | Distribute traffic, terminate TLS | TLS termination, health checks, traffic distribution, WebSocket upgrade | Managed service; scales automatically with connection count | Complete frontend unavailability; DNS failover to secondary | HTTPS inbound; HTTP/2 to Ingress |
| **Nginx Ingress** | In-cluster HTTP routing | Path-based routing, rate limiting, security headers, CORS, gzip | Horizontal: multiple Ingress controller replicas | API and UI unreachable; pods healthy but no traffic | HTTP from LB; HTTP to ClusterIP Services |
| **Frontend (Next.js)** | Serve web UI, SSR pages | Server-side rendering, client hydration, asset serving, API proxying | HPA: CPU ≥ 70% → scale up; min 3, max 10 | Degraded UX; API still accessible directly | HTTP from Ingress; HTTP to Backend Service |
| **Backend (FastAPI)** | Business logic, API | Auth, tenancy, RBAC, CRUD, job enqueueing, SSE/WS, signed URL generation | HPA: CPU ≥ 70% or request latency p99 > 500ms; min 3, max 15 | All mutations and queries fail; workers continue processing existing jobs | HTTP from Ingress/Frontend; TCP to PostgreSQL, Redis; HTTPS to AI providers, Object Storage |
| **Redis** | Cache, queue, pub/sub | Session cache, Celery queues, rate-limit counters, progress pub/sub | Vertical: increase memory tier. HA: primary + replica with sentinel | Cache: degraded performance. Queue: workers idle, jobs delayed. Pub/sub: progress updates lost | TCP 6379 from Backend and Workers |
| **OCR Worker** | Process textbook uploads | PDF-to-image, OCR, structural parsing, asset extraction | HPA: queue depth `ocr:waiting` > 50; min 1, max 8 | Textbook processing halted; uploads queue indefinitely | TCP to Redis (consume), PostgreSQL (write), Object Storage (read/write) |
| **Generation Worker** | Generate question papers | Retrieval, AI generation, validation, question bank insertion | HPA: queue depth `generation:waiting` > 20; min 1, max 6 | Paper generation halted; teachers see "generating" indefinitely | TCP to Redis, PostgreSQL; HTTPS to AI providers |
| **Export Worker** | Render papers to PDF/DOCX | Template application, PDF rendering, DOCX generation, upload to storage | HPA: queue depth `export:waiting` > 30; min 1, max 6 | Export downloads unavailable; papers exist but cannot be downloaded | TCP to Redis, PostgreSQL, Object Storage |
| **PostgreSQL** | System of record | All transactional data, RLS tenant isolation; embeddings only after ADR-007 adoption | Vertical: increase CPU/memory/IOPS. Read replicas for query scaling | Primary failure: promote replica, brief write outage (< 30s). Full failure: restore from backup (RTO ≤ 4h) | TCP 5432 from Backend and Workers |
| **Object Storage** | Binary asset persistence | Textbook uploads, extracted pages, paper exports, template assets | Managed service; effectively unlimited; scales with usage | Upload and download failures; processing pipeline stalled | HTTPS from Backend (signed URLs), Workers, and Browser |
| **Gemini API** | LLM and embedding inference | Question generation, validation, difficulty classification, embedding computation | External; managed by provider; subject to rate limits | Generation degraded; retrieval-only mode activates; circuit breaker trips | HTTPS outbound from Generation Worker and Backend |
| **Prometheus** | Metrics collection | Scrape metrics from all components; evaluate alerting rules | Vertical: increase retention storage | Metrics gaps; alerting blind spots | HTTP scrape endpoints on all pods |
| **Grafana** | Dashboards | Visualize metrics, logs, and traces for operators | Single replica with persistent storage | Dashboard access lost; monitoring data still collected | HTTP to Prometheus, Loki, Jaeger |
| **Loki** | Log aggregation | Ingest, index, and query structured logs from all pods | Horizontal: scale ingesters and queriers | Log search unavailable; logs still emitted to stdout | HTTP from log shipping agents (Promtail) |
| **Jaeger** | Distributed tracing | Trace collection, storage, and query | Horizontal: scale collectors | Trace data lost for the outage period; no impact on application | HTTP/gRPC from OpenTelemetry SDKs |

### Communication Matrix

| Source | Destination | Protocol | Port | Direction |
|--------|-------------|----------|------|-----------|
| Browser | CDN | HTTPS | 443 | Outbound |
| Browser | Object Storage | HTTPS | 443 | Outbound (signed URLs) |
| CDN | Load Balancer | HTTPS | 443 | Outbound |
| Load Balancer | Nginx Ingress | HTTP/2 | 80/443 | Inbound |
| Nginx Ingress | Frontend Service | HTTP | 3000 | Internal |
| Nginx Ingress | Backend Service | HTTP | 8000 | Internal |
| Frontend | Backend Service | HTTP | 8000 | Internal |
| Backend | PostgreSQL | TCP | 5432 | Internal |
| Backend | Redis | TCP | 6379 | Internal |
| Backend | Object Storage | HTTPS | 443 | Outbound |
| Backend | Gemini API | HTTPS | 443 | Outbound |
| Workers (all) | Redis | TCP | 6379 | Internal |
| Workers (all) | PostgreSQL | TCP | 5432 | Internal |
| Workers (OCR, Export) | Object Storage | HTTPS | 443 | Outbound |
| Workers (Generation) | Gemini API | HTTPS | 443 | Outbound |
| Prometheus | All Pods | HTTP | 9090/metrics | Internal |
| Promtail | Loki | HTTP | 3100 | Internal |
| All Pods | Jaeger Collector | HTTP/gRPC | 14268/14250 | Internal |

---

## 5. Environments

### Environment Matrix

| Aspect | Development (Local) | Testing (CI) | Staging | Production |
|--------|---------------------|--------------|---------|------------|
| **Orchestration** | Docker Compose | Docker Compose | Kubernetes (same manifests, smaller scale) | Kubernetes (full HA) |
| **Replicas** | 1 per service | 1 per service | 1–2 per service | 3+ per service (HPA) |
| **Database** | PostgreSQL in container | PostgreSQL in container | Managed PostgreSQL (single node) | Managed PostgreSQL (primary + replicas) |
| **Redis** | Redis in container (single) | Redis in container (single) | Managed Redis (single node) | Managed Redis (primary + replica + sentinel) |
| **Object Storage** | MinIO in container | MinIO in container | S3-compatible managed (dev bucket) | S3-compatible managed (prod buckets) |
| **AI Providers** | Mock adapter or dev-tier API keys | Mock adapter (deterministic) | Dev-tier API keys (rate-limited) | Production API keys (full quota) |
| **Domain** | localhost:3000 | N/A (headless) | staging.example.com | app.example.com |
| **TLS** | Self-signed or none | None | Let's Encrypt / managed cert | Managed certificate (auto-renewed) |
| **CDN** | None | None | Optional (Cloudflare dev) | Full CDN (Cloudflare / CloudFront) |
| **Monitoring** | Docker healthchecks only | CI test assertions | Prometheus + Grafana (shared) | Full stack (Prometheus, Grafana, Loki, Jaeger, Alertmanager) |
| **Secrets** | `.env` file (git-ignored) | GitHub Actions secrets | Kubernetes Secrets (sealed) | Kubernetes Secrets (sealed) + external vault |
| **Data** | Seed data (dev fixtures) | Ephemeral (reset per run) | Anonymized subset of prod | Real tenant data |
| **Backups** | None | None | Daily automated | Continuous WAL + daily full + hourly incremental |

### Configuration Strategy

All configuration follows the Twelve-Factor pattern: environment variables injected at runtime, never baked into images.

**Configuration hierarchy** (highest precedence first):

1. **Runtime environment variables** — Kubernetes ConfigMaps and Secrets, or Docker Compose `.env` files.
2. **Default values in application code** — Safe defaults for development (e.g., `LOG_LEVEL=debug`, `REDIS_HOST=localhost`).
3. **Build-time constants** — Only for values that genuinely never change (e.g., API version prefix `/api/v1`).

**Environment variable categories:**

| Category | Examples | Storage |
|----------|----------|---------|
| Application config | `PORT`, `LOG_LEVEL`, `NODE_ENV`, `CORS_ORIGINS` | ConfigMap |
| Database | `DATABASE_URL`, `DATABASE_POOL_SIZE`, `DATABASE_SSL` | Secret |
| Redis | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` | Secret |
| Object Storage | `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Secret |
| AI Provider | `GEMINI_API_KEY`, `AI_MODEL_GENERATE`, `AI_MODEL_CLASSIFY`, `AI_EMBEDDING_MODEL` | Secret (key) + ConfigMap (model IDs) |
| Auth | `JWT_SECRET`, `JWT_EXPIRY`, `REFRESH_TOKEN_EXPIRY` | Secret |
| Feature flags | `FF_ENABLE_AI_GENERATION`, `FF_ENABLE_BULK_EXPORT` | ConfigMap |

### Secrets Management

- **Development:** `.env` files excluded from version control via `.gitignore`. Developers use `.env.example` as a template.
- **CI:** GitHub Actions encrypted secrets, injected as environment variables during builds.
- **Staging/Production:** Kubernetes Secrets, encrypted at rest with Sealed Secrets or an external secrets operator (AWS Secrets Manager, HashiCorp Vault). Secrets are never logged, never printed in error messages, and never included in container images.

---

## 6. Docker Architecture

### Container Inventory

| Container | Base Image | Exposed Port | Volumes | Resource Profile |
|-----------|-----------|--------------|---------|-----------------|
| `web` (Frontend) | `node:22-alpine` | 3000 | None (stateless) | 256 MB RAM, 0.25 CPU |
| `api` (Backend) | `python:3.12-slim` | 8000 | None (stateless) | 512 MB RAM, 0.5 CPU |
| `worker-ocr` | `python:3.12-slim` + OCR/render libs | None | None (stateless) | 1 GB RAM, 1.0 CPU |
| `worker-generation` | `python:3.12-slim` | None | None (stateless) | 512 MB RAM, 0.5 CPU |
| `worker-export` | `python:3.12-slim` + Chromium | None | None (stateless) | 1 GB RAM, 0.5 CPU |
| `postgres` (dev only) | `postgres:16` (swap to `pgvector/pgvector:pg16` only on ADR-007 adoption) | 5432 | `pgdata:/var/lib/postgresql/data` | 1 GB RAM, 1.0 CPU |
| `redis` (dev only) | `redis:7-alpine` | 6379 | `redisdata:/data` | 256 MB RAM, 0.25 CPU |
| `minio` (dev only) | `minio/minio:latest` | 9000, 9001 | `miniodata:/data` | 512 MB RAM, 0.25 CPU |
| `prometheus` (dev opt.) | `prom/prometheus:latest` | 9090 | `promdata:/prometheus` | 256 MB RAM, 0.25 CPU |
| `grafana` (dev opt.) | `grafana/grafana:latest` | 3002 | `grafanadata:/var/lib/grafana` | 256 MB RAM, 0.25 CPU |

### Image Build Strategy

All application images use **multi-stage builds** to minimize image size and eliminate build-time dependencies from the runtime image.

The backend and the frontend have **different Dockerfiles with different base images and different
toolchains** — the separation is physical at the image layer, not a convention.

**Backend (`Dockerfile.api`, `Dockerfile.worker` — Python):**

1. *Dependencies* — install into a virtualenv from a locked `requirements.txt` / `uv.lock`. Cached
   aggressively; rebuilds only when the lock file changes.
2. *Build* — copy source; compile nothing (Python), but byte-compile and run a smoke import so a broken
   module fails the build rather than the deploy.
3. *Runtime* — clean `python:3.12-slim` base, copy only the virtualenv and source, run as a non-root
   user, expose 8000 (API only; workers expose nothing), define `HEALTHCHECK`.

**Frontend (`Dockerfile.web` — Node):**

1. *Dependencies* — `pnpm install --frozen-lockfile`.
2. *Build* — Next.js production build with standalone output.
3. *Runtime* — clean `node:22-alpine` base, copy the standalone bundle only, `NODE_ENV=production`,
   non-root, expose 3000, `HEALTHCHECK`.

**Image sizing targets:**
- Frontend: ≤ 250 MB
- Backend (api): ≤ 350 MB
- Workers: ≤ 600 MB (OCR and export workers carry render/OCR libraries)

A `.dockerignore` is mandatory in every build context. Without it the first `COPY . .` bakes `.env`
into an image layer permanently, where it survives every later `RUN rm`.

### Image Versioning

Images are tagged with a compound version: `<service>:<git-sha>-<timestamp>`. Example: `api:a1b2c3d-20260730T1200Z`.

- `latest` is never used in production. Every deployment references an exact image tag.
- `main` branch builds produce `<service>:main-<sha>` tags.
- Release branches produce `<service>:v1.2.3` semantic version tags.
- All images are pushed to a private container registry (GitHub Container Registry, AWS ECR, or Google Artifact Registry).

### Docker Networking

**Development (Docker Compose):**

```
┌─────────────────────────────────────────────────────────┐
│              Docker Network: aipaper-network             │
│                   (bridge, internal)                     │
│                                                          │
│  ┌──────┐  ┌─────┐  ┌───────┐  ┌───────┐  ┌──────────┐│
│  │ web  │  │ api │  │ redis │  │ postgres│ │  minio   ││
│  │:3000 │  │:3001│  │:6379  │  │:5432   │ │:9000     ││
│  └──┬───┘  └──┬──┘  └───┬───┘  └───┬────┘ └────┬─────┘│
│     │         │         │          │            │       │
│     └─────────┴─────────┴──────────┴────────────┘       │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ worker-ocr   │  │ worker-gen   │  │ worker-export│   │
│  └──────────────┘  └──────────────┘  └──────────────┘   │
└─────────────────────────────────────────────────────────┘
         │
    Host: localhost:3000 (web), localhost:5432 (pg), etc.
```

All application containers join a single bridge network (`aipaper-network`). Containers reference each other by service name (e.g., `postgres:5432`, `redis:6379`). Only the frontend port (3000) and optionally database ports are published to the host.

### 6.5 Local Development — Separate Services, Separate Log Streams

The backend and the frontend are **separate Compose services**. They are separate images, separate
processes, and separate log streams. There is no configuration in which the backend runs inside the
frontend's process, and no configuration in which their logs are merged into one undifferentiated
stream.

```yaml
# infra/docker-compose.yml  (abridged — the shape that matters)
services:
  api:                          # FastAPI — the backend, its own process
    build: { context: ., dockerfile: Dockerfile.api }
    command: uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
    ports: ["8000:8000"]
    env_file: [backend/.env]   # DATABASE_URL, REDIS_URL, GEMINI_API_KEY, JWT keys
    depends_on: [postgres, redis, minio]

  worker-generation:            # Celery — its own process, its own stream
    build: { context: ., dockerfile: Dockerfile.worker }
    command: celery -A src.celery_app worker -Q generation -c 3 --loglevel=info
    env_file: [backend/.env]
    depends_on: [redis, postgres]

  worker-ocr:
    build: { context: ., dockerfile: Dockerfile.worker }
    command: celery -A src.celery_app worker -Q ocr -c 2 --loglevel=info
    env_file: [backend/.env]
    depends_on: [redis, postgres, minio]

  web:                          # Next.js — the frontend, a DIFFERENT process
    build: { context: ., dockerfile: Dockerfile.web }
    command: pnpm dev
    ports: ["3000:3000"]
    env_file: [frontend/.env.local]   # NEXT_PUBLIC_* and API_INTERNAL_URL only.
                                      # No DATABASE_URL. No GEMINI_API_KEY. No JWT secret.
    depends_on: [api]

  postgres:
    image: postgres:16          # pgvector image only when ADR-007 adoption happens
  redis:
    image: redis:7-alpine
  minio:
    image: minio/minio:latest
```

#### Reading just the backend's logs

```bash
docker compose up -d                          # start everything
docker compose logs -f api                    # BACKEND ONLY — nothing from the frontend
docker compose logs -f worker-generation      # one worker only
docker compose logs -f api worker-generation  # both, each line still labelled by service
docker compose logs -f web                    # frontend only, for contrast
```

Every backend line is structured JSON on stdout carrying `"service": "api"` (or the worker name) and
the request/correlation ID, so the streams stay separable by field even when aggregated. For local
readability, set `LOG_FORMAT=pretty`; production always uses `json`.

#### Without Docker

Two terminals, two processes — the separation is the same:

```bash
# terminal 1 — backend
cd backend && uvicorn src.main:app --reload --port 8000

# terminal 2 — frontend
cd frontend && pnpm dev
```

`pnpm dev` at the repo root must **not** be wired to start the API. Aggregating both into one terminal
is exactly the merged-log-stream failure this section exists to prevent.

### Volumes and Persistent Storage

| Volume | Mount Point | Purpose | Backup Required |
|--------|-------------|---------|-----------------|
| `pgdata` | `/var/lib/postgresql/data` | PostgreSQL data directory | Yes (dev: optional, prod: critical) |
| `redisdata` | `/data` | Redis AOF/RDB persistence | Yes (prod: recommended) |
| `miniodata` | `/data` | MinIO object data | No (dev artifacts are disposable) |
| `promdata` | `/prometheus` | Prometheus TSDB | No (metrics are reconstructible) |
| `grafanadata` | `/var/lib/grafana` | Grafana dashboards and settings | Yes (dashboards as code preferred) |

In production, PostgreSQL and Redis use managed services with provider-managed storage. Application containers use no persistent volumes — they are fully stateless.

---

## 7. Kubernetes Architecture *(scale target — not the MVP topology)*

> **Read this section as the destination, not the starting point.** MVP and Beta run on **ECS Fargate**
> (§7.0). The manifests below are written and validated in CI so the migration is a configuration
> exercise, but they are not applied to a production cluster until one of the adoption triggers in §1
> fires. See [ADR-013](ADR/ADR-013-ecs-fargate-before-kubernetes.md).

### 7.0 The MVP topology: ECS Fargate

```
Route53 ─► CloudFront ─► ALB ─► ECS Fargate (private subnets)
                                  ├─ api                (1–2 tasks, autoscale on CPU + request count)
                                  ├─ web                (1–2 tasks, autoscale on CPU)
                                  ├─ worker-ocr         (1 task,   autoscale on queue depth)
                                  ├─ worker-generation  (1 task,   autoscale on queue depth)
                                  └─ worker-export      (1 task,   autoscale on queue depth)
Fargate ─► RDS PostgreSQL (Multi-AZ)  [private]   # pg_trgm + citext; no pgvector at MVP
Fargate ─► ElastiCache Redis (Multi-AZ) [private] # cache + Celery broker + pub/sub
Fargate ─► S3 (uploads/exports) via VPC endpoint
Fargate ─► Secrets Manager / KMS / CloudWatch
Egress  ─► NAT ─► Gemini API (allowlisted), Mathpix (allowlisted)
```

| Concern | How Fargate covers it |
|---|---|
| Rolling deploys | Native deployment controller with `minimumHealthyPercent` / `maximumPercent` |
| Health-gated traffic | ALB target-group health checks against `/api/health/ready` |
| Autoscaling | Application Auto Scaling on CPU and ALB request count; workers on a CloudWatch queue-depth metric published by the Celery exporter |
| **Independent log streams** | One CloudWatch log group per service — `/<env>/api`, `/<env>/web`, `/<env>/worker-*`. The backend's logs are readable without the frontend's interleaved in. |
| Secrets | Task-definition secrets injected from Secrets Manager; no long-lived keys in images |
| IAM | One task role per service, least-privilege |
| Rollback | Redeploy the previous task-definition revision — immutable and SHA-tagged |

Every Kubernetes concept in the rest of this section has a Fargate equivalent: Deployment → service,
HPA → Application Auto Scaling policy, Secret → task-definition secret, ServiceAccount → task role,
NetworkPolicy → security group. The mapping is intentionally clean, which is what makes the later
migration mechanical.

### Namespace Organization

```
┌─────────────────────────────────────────────┐
│             Kubernetes Cluster               │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  Namespace: ingress-nginx              │  │
│  │  • Nginx Ingress Controller            │  │
│  │  • Ingress resources                   │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  Namespace: aipaper-app                │  │
│  │  • Frontend Deployment + Service       │  │
│  │  • Backend Deployment + Service        │  │
│  │  • ConfigMaps, Secrets                 │  │
│  │  • HPA for Frontend and Backend        │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  Namespace: aipaper-workers            │  │
│  │  • OCR Worker Deployment               │  │
│  │  • Generation Worker Deployment        │  │
│  │  • Export Worker Deployment            │  │
│  │  • HPA for each worker type            │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  Namespace: aipaper-data               │  │
│  │  • PostgreSQL StatefulSet (if self-    │  │
│  │    managed) or ExternalName Service    │  │
│  │  • Redis StatefulSet (if self-managed) │  │
│  │    or ExternalName Service             │  │
│  │  • PersistentVolumeClaims              │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  Namespace: aipaper-monitoring         │  │
│  │  • Prometheus                          │  │
│  │  • Grafana                             │  │
│  │  • Loki                                │  │
│  │  • Jaeger                              │  │
│  │  • Alertmanager                        │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  Namespace: cert-manager               │  │
│  │  • cert-manager controller             │  │
│  │  • ClusterIssuer (Let's Encrypt)       │  │
│  └────────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

**Rationale for namespace separation:**
- **Blast radius containment.** A misconfigured resource limit in the workers namespace cannot starve the app namespace.
- **RBAC boundaries.** CI/CD service accounts can deploy to `aipaper-app` and `aipaper-workers` without access to `aipaper-data` or `aipaper-monitoring`.
- **Network policy enforcement.** Network policies restrict cross-namespace traffic to only the required paths (e.g., app → data, workers → data).
- **Resource quota management.** Each namespace has independent CPU and memory quotas, preventing any one component from consuming cluster resources.

### Deployments

| Deployment | Namespace | Replicas (min/max) | Update Strategy | Pod Disruption Budget |
|------------|-----------|--------------------|-----------------|-----------------------|
| `frontend` | `aipaper-app` | 3 / 10 | RollingUpdate (maxSurge: 1, maxUnavailable: 0) | minAvailable: 2 |
| `backend` | `aipaper-app` | 3 / 15 | RollingUpdate (maxSurge: 1, maxUnavailable: 0) | minAvailable: 2 |
| `worker-ocr` | `aipaper-workers` | 1 / 8 | RollingUpdate (maxSurge: 1, maxUnavailable: 1) | minAvailable: 1 |
| `worker-generation` | `aipaper-workers` | 1 / 6 | RollingUpdate (maxSurge: 1, maxUnavailable: 1) | minAvailable: 1 |
| `worker-export` | `aipaper-workers` | 1 / 6 | RollingUpdate (maxSurge: 1, maxUnavailable: 1) | minAvailable: 1 |

**Pod anti-affinity:** Frontend and backend pods use `podAntiAffinity` with `preferredDuringSchedulingIgnoredDuringExecution` to spread replicas across nodes and availability zones. This ensures that a single node failure does not take down all replicas of a service.

**Graceful shutdown:** All pods set `terminationGracePeriodSeconds: 30`. FastAPI (uvicorn), Celery, and Next.js each handle `SIGTERM` by stopping new request acceptance, draining active connections, and completing in-flight queue jobs before exiting.

### Services

| Service | Namespace | Type | Port | Selector |
|---------|-----------|------|------|----------|
| `frontend` | `aipaper-app` | ClusterIP | 3000 | `app=frontend` |
| `backend` | `aipaper-app` | ClusterIP | 8000 | `app=backend` |
| `postgres` | `aipaper-data` | ClusterIP / ExternalName | 5432 | `app=postgres` or external endpoint |
| `redis` | `aipaper-data` | ClusterIP / ExternalName | 6379 | `app=redis` or external endpoint |

Workers do not expose Services — they are consumers, not servers. They connect to Redis and PostgreSQL as clients.

### Ingress

A single Ingress resource in the `aipaper-app` namespace routes external traffic:

```
Ingress: aipaper-ingress
  Host: app.example.com
  TLS: cert-manager managed certificate
  Rules:
    /api/*      → backend:8000
    /api/health → backend:8000
    /*          → frontend:3000
  Annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"     # textbook upload limit
    nginx.ingress.kubernetes.io/proxy-read-timeout: "120"    # generation can take time
    nginx.ingress.kubernetes.io/rate-limit-connections: "50"
    nginx.ingress.kubernetes.io/enable-cors: "true"
    nginx.ingress.kubernetes.io/websocket-services: "backend"
```

### ConfigMaps and Secrets

**ConfigMaps** store non-sensitive configuration:

| ConfigMap | Namespace | Keys |
|-----------|-----------|------|
| `app-config` | `aipaper-app` | `NODE_ENV`, `LOG_LEVEL`, `CORS_ORIGINS`, `API_VERSION`, feature flags |
| `worker-config` | `aipaper-workers` | `QUEUE_CONCURRENCY`, `JOB_TIMEOUT`, `MAX_RETRIES`, `DLQ_ENABLED` |

**Secrets** store sensitive configuration, encrypted at rest:

| Secret | Namespace | Keys |
|--------|-----------|------|
| `db-credentials` | `aipaper-app`, `aipaper-workers` | `DATABASE_URL`, `DATABASE_POOL_SIZE` |
| `redis-credentials` | `aipaper-app`, `aipaper-workers` | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` |
| `storage-credentials` | `aipaper-app`, `aipaper-workers` | `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` |
| `ai-credentials` | `aipaper-app`, `aipaper-workers` | `GEMINI_API_KEY` |
| `auth-secrets` | `aipaper-app` | `JWT_SECRET`, `JWT_REFRESH_SECRET` |

### Horizontal Pod Autoscaler

| HPA Target | Namespace | Metric | Threshold | Min | Max | Cooldown |
|-----------|-----------|--------|-----------|-----|-----|----------|
| `frontend` | `aipaper-app` | CPU utilization | 70% | 3 | 10 | Scale-up: 60s, Scale-down: 300s |
| `backend` | `aipaper-app` | CPU utilization | 70% | 3 | 15 | Scale-up: 60s, Scale-down: 300s |
| `backend` | `aipaper-app` | Request latency p99 | 500ms | 3 | 15 | Scale-up: 30s, Scale-down: 300s |
| `worker-ocr` | `aipaper-workers` | Custom: `celery_queue_waiting{queue="ocr"}` | 50 | 1 | 8 | Scale-up: 30s, Scale-down: 600s |
| `worker-generation` | `aipaper-workers` | Custom: `celery_queue_waiting{queue="generation"}` | 20 | 1 | 6 | Scale-up: 30s, Scale-down: 600s |
| `worker-export` | `aipaper-workers` | Custom: `celery_queue_waiting{queue="export"}` | 30 | 1 | 6 | Scale-up: 30s, Scale-down: 600s |

**Custom metrics** are exposed by Celery metrics exporters and scraped by Prometheus. The Kubernetes Metrics Adapter (Prometheus Adapter or KEDA) makes these metrics available to the HPA controller.

### Node Architecture

```
┌───────────────────────────────────────────────────────────┐
│                    Kubernetes Node Pool                     │
│                                                            │
│  ┌────────────────────────────────────────────────────┐   │
│  │  Node Pool: app-pool                                │   │
│  │  Instance type: 4 vCPU, 8 GB RAM                   │   │
│  │  Nodes: 3 (across 3 AZs)                           │   │
│  │  Taints: none                                       │   │
│  │  Workloads: Frontend, Backend, Ingress              │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  ┌────────────────────────────────────────────────────┐   │
│  │  Node Pool: worker-pool                             │   │
│  │  Instance type: 4 vCPU, 16 GB RAM                  │   │
│  │  Nodes: 2–6 (autoscaling)                          │   │
│  │  Taints: workload=worker:NoSchedule                │   │
│  │  Tolerations: worker pods only                     │   │
│  │  Workloads: OCR, Generation, Export workers         │   │
│  │  Option: Spot/Preemptible instances                │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  ┌────────────────────────────────────────────────────┐   │
│  │  Node Pool: monitoring-pool                         │   │
│  │  Instance type: 2 vCPU, 8 GB RAM                   │   │
│  │  Nodes: 1–2                                        │   │
│  │  Taints: workload=monitoring:NoSchedule            │   │
│  │  Workloads: Prometheus, Grafana, Loki, Jaeger       │   │
│  └────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────┘
```

**Rationale for separate node pools:**
- **Resource isolation.** Worker CPU spikes during OCR processing do not contend with API request handling.
- **Cost optimization.** Workers tolerate interruption and can run on cheaper spot instances. API and frontend require on-demand instances for reliability.
- **Independent scaling.** Worker node pool scales up during bulk processing periods without affecting the app node pool.

---

## 8. Networking

### HTTPS and TLS

All external traffic is encrypted with TLS 1.2+ (TLS 1.3 preferred). Certificates are managed automatically:

- **Production:** cert-manager with Let's Encrypt (ACME DNS-01 or HTTP-01 challenge). Certificates auto-renew 30 days before expiration.
- **Staging:** cert-manager with Let's Encrypt staging CA.
- **Development:** Self-signed certificates or plain HTTP (localhost only).

**TLS termination** occurs at the load balancer for external traffic. Internal cluster communication uses unencrypted HTTP over the private network, which is acceptable because:
- The Kubernetes cluster network is isolated (VPC-internal).
- Network policies restrict pod-to-pod communication.
- For higher security requirements, a service mesh (Istio/Linkerd) can add mTLS for internal traffic.

### DNS Configuration

| Record | Type | Value | Purpose |
|--------|------|-------|---------|
| `app.example.com` | A / CNAME | Load Balancer IP / CDN endpoint | Production frontend + API |
| `staging.example.com` | A / CNAME | Staging Load Balancer | Staging environment |
| `api.example.com` | CNAME | `app.example.com` | Optional: separate API domain |
| `storage.example.com` | CNAME | S3 endpoint | Optional: custom storage domain |
| `monitoring.internal.example.com` | A | Internal IP (VPN-only) | Grafana, Prometheus (internal access only) |

### Load Balancer Architecture

```
                    Internet
                       │
                       ▼
            ┌──────────────────┐
            │   DNS (Route 53  │
            │   / Cloudflare)  │
            └────────┬─────────┘
                     │
                     ▼
            ┌──────────────────┐
            │  Cloud Load      │
            │  Balancer (L7)   │
            │                  │
            │  • TLS 1.3       │
            │  • HTTP/2        │
            │  • WebSocket     │
            │  • Health checks │
            │  • Sticky: none  │
            │  • Algo: round   │
            │    robin         │
            └────────┬─────────┘
                     │
              ┌──────┴──────┐
              │  Kubernetes  │
              │  NodePort /  │
              │  Ingress     │
              └─────────────┘
```

**No sticky sessions.** The backend is stateless; session data lives in Redis. Any backend pod can handle any request.

### Reverse Proxy (Nginx Ingress)

Nginx handles:
- **Path-based routing:** `/api/*` → backend, `/*` → frontend.
- **Rate limiting:** 100 requests/second per IP for API, 500 requests/second per IP for static assets.
- **Request size limits:** 100 MB max body size for textbook uploads.
- **Timeouts:** 120-second proxy read timeout for long-running generation requests.
- **Compression:** gzip for text/html, application/json, text/css, application/javascript.
- **Security headers:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection: 1; mode=block`, `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
- **CORS:** Allow `app.example.com` origin; restrict methods to GET, POST, PUT, PATCH, DELETE; allow credentials.

### Internal Communication

```
┌──────────────────────────────────────────────────────────────┐
│                   Private Network (VPC)                        │
│                                                                │
│  ┌───────────────┐         ┌───────────────┐                  │
│  │  aipaper-app  │────────▶│ aipaper-data  │                  │
│  │  namespace     │  TCP    │  namespace     │                  │
│  │               │  5432   │  (PostgreSQL)  │                  │
│  │  Frontend ────▶│  6379   │  (Redis)       │                  │
│  │  Backend      │         │               │                  │
│  └───────────────┘         └───────────────┘                  │
│         ▲                          ▲                           │
│         │                          │                           │
│  ┌──────┴────────┐                │                           │
│  │aipaper-workers│────────────────┘                           │
│  │  namespace     │  TCP 5432, 6379                            │
│  │               │                                            │
│  │  OCR Worker   │                                            │
│  │  Gen Worker   │─────────────────▶ External AI APIs (HTTPS) │
│  │  Export Worker│─────────────────▶ Object Storage (HTTPS)   │
│  └───────────────┘                                            │
│                                                                │
│  NetworkPolicy rules:                                         │
│  • aipaper-app    → aipaper-data: ALLOW (TCP 5432, 6379)     │
│  • aipaper-workers → aipaper-data: ALLOW (TCP 5432, 6379)    │
│  • aipaper-app    → aipaper-workers: DENY                    │
│  • aipaper-workers → aipaper-app: DENY                       │
│  • aipaper-monitoring → ALL: ALLOW (metrics scrape)          │
│  • Default: DENY all cross-namespace traffic                  │
└──────────────────────────────────────────────────────────────┘
```

### Firewall Rules

| Rule | Source | Destination | Port | Action |
|------|--------|-------------|------|--------|
| HTTPS Inbound | 0.0.0.0/0 | Load Balancer | 443 | ALLOW |
| HTTP Redirect | 0.0.0.0/0 | Load Balancer | 80 | ALLOW (redirect to 443) |
| SSH Admin | Admin VPN CIDR | Bastion host | 22 | ALLOW |
| Monitoring | Admin VPN CIDR | Grafana | 3002 | ALLOW |
| All other inbound | 0.0.0.0/0 | * | * | DENY |
| Outbound AI APIs | Workers/Backend | AI Provider IPs | 443 | ALLOW |
| Outbound Storage | Workers/Backend | S3 endpoint | 443 | ALLOW |
| Outbound DNS | All | DNS resolver | 53 | ALLOW |
| Outbound NTP | All | NTP servers | 123 | ALLOW |
| All other outbound | * | * | * | DENY (with exceptions for package repos during builds) |

### Port Usage Summary

| Port | Service | Protocol | Exposure |
|------|---------|----------|----------|
| 443 | Load Balancer | HTTPS | External |
| 80 | Load Balancer (redirect) | HTTP | External |
| 3000 | Frontend (Next.js) | HTTP | Internal (ClusterIP) |
| 8000 | Backend (FastAPI/uvicorn) | HTTP | Internal |
| 5432 | PostgreSQL | TCP | Internal (ClusterIP) |
| 6379 | Redis | TCP | Internal (ClusterIP) |
| 9090 | Prometheus | HTTP | Internal (monitoring namespace) |
| 3002 | Grafana | HTTP | Internal (VPN-accessible) |
| 3100 | Loki | HTTP | Internal (monitoring namespace) |
| 14268 | Jaeger Collector (HTTP) | HTTP | Internal (monitoring namespace) |
| 9100 | Node Exporter | HTTP | Internal (Prometheus scrape) |

---

## 9. CI/CD Pipeline

### GitHub Repository Structure

The monorepo (`ai-paper`) contains all application code, shared packages, infrastructure manifests, and documentation:

```
ai-paper/
├── apps/
│   ├── web/              # Next.js frontend
│   ├── api/              # FastAPI backend (Python)
│   └── workers/          # Celery workers: OCR, Generation, Export (Python)
├── packages/             # Shared contracts, db, config, events
├── infra/                # Docker Compose, Kubernetes manifests
├── docs/                 # Architecture documentation
├── .github/
│   └── workflows/        # CI/CD pipeline definitions
├── Dockerfile.web
├── Dockerfile.api
├── Dockerfile.worker-ocr
├── Dockerfile.worker-generation
├── Dockerfile.worker-export
└── docker-compose.yml
```

### Branch Strategy

| Branch | Purpose | Merges From | Deploys To | Protection |
|--------|---------|-------------|------------|------------|
| `main` | Production-ready code | `develop` via PR | Production (after staging validation) | Required reviews: 2, status checks, no force push |
| `develop` | Integration branch | Feature branches via PR | Staging (automatically) | Required reviews: 1, status checks |
| `feature/*` | Feature development | — | Preview environment (optional) | No protection |
| `hotfix/*` | Emergency production fixes | — | Staging → Production (fast-tracked) | Required reviews: 1 |
| `release/v*` | Release candidates | `develop` | Staging for final validation | Required reviews: 2 |

### Pull Request Workflow

1. Developer creates a `feature/*` branch from `develop`.
2. Developer pushes commits. CI runs on every push.
3. Developer opens a PR to `develop`. CI runs the full pipeline.
4. Code review: minimum 1 approval for develop, 2 for main.
5. All status checks must pass: lint, typecheck, unit tests, integration tests, security scan, build.
6. PR is squash-merged to `develop`. Staging auto-deploys.
7. After staging validation, `develop` is PR-merged to `main`. Production deploys.

### CI/CD Pipeline Stages

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CI/CD PIPELINE                                  │
│                                                                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │  TRIGGER  │───▶│  BUILD   │───▶│   TEST   │───▶│ SECURITY │          │
│  │           │    │          │    │          │    │   SCAN   │          │
│  │ PR opened │    │ Install  │    │ Lint     │    │          │          │
│  │ Push to   │    │ deps     │    │ Typecheck│    │ npm audit│          │
│  │ branch    │    │ Compile  │    │ Unit     │    │ Trivy    │          │
│  │           │    │ TS       │    │ tests    │    │ image    │          │
│  └──────────┘    │ Generate  │    │ Integra- │    │ scan     │          │
│                  │ Alembic  │    │ tion     │    │ SAST     │          │
│                  └──────────┘    │ tests    │    └────┬─────┘          │
│                                  └──────────┘         │                 │
│                                                       ▼                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │  DEPLOY  │◀───│ PUBLISH  │◀───│  DOCKER  │◀───│ ARTIFACT │          │
│  │          │    │          │    │  BUILD   │    │  STORE   │          │
│  │ kubectl  │    │ Push to  │    │          │    │          │          │
│  │ apply    │    │ registry │    │ Multi-   │    │ Upload   │          │
│  │ Rolling  │    │ Tag with │    │ stage    │    │ test     │          │
│  │ update   │    │ SHA +    │    │ build    │    │ results  │          │
│  │          │    │ semver   │    │ All      │    │ Coverage │          │
│  │ Health   │    │          │    │ services │    │ reports  │          │
│  │ check    │    │          │    │          │    │          │          │
│  │ verify   │    │          │    │          │    │          │          │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘          │
│                                                                          │
│  On failure at any stage: pipeline halts, PR blocked, team notified.    │
└─────────────────────────────────────────────────────────────────────────┘
```

### Pipeline Detail

**Stage 1 — Build** *(backend and frontend build independently and in parallel)*
- Checkout code.
- **Backend:** install from the locked Python requirements; byte-compile; smoke-import every module.
- **Frontend:** `pnpm install --frozen-lockfile`; build the Next.js production bundle.

**Stage 2 — Test**
- **Backend:** `ruff` lint + format check, `mypy` strict type checking, `pytest` with coverage
  threshold (>= 80% on core), including the **solver property tests** and the **answer-correctness
  gate** (see `06_AI_Architecture.md` section 10.3 — a regression in correctness pass rate fails the
  build).
- **Frontend:** ESLint, Prettier check, TypeScript strict typecheck, Vitest component tests.
- **Contract:** regenerate the client's types from the FastAPI OpenAPI document and fail if the
  committed types drift.
- Integration tests against ephemeral PostgreSQL and Redis containers, **with RLS enabled and the
  pooled-connection tenant-isolation test included**.
- `alembic upgrade head` dry-run against a fresh database, then a one-revision downgrade.

**Stage 3 — Security Scan**
- `pip-audit` (Python) and `npm audit` (frontend) for dependency vulnerabilities.
- Prompt-injection adversarial suite against the AI module.
- Trivy or Snyk scan on Docker images for CVEs.
- Static Application Security Testing (SAST) for code vulnerabilities.
- Secret detection scan (prevent committed secrets).

**Stage 4 — Docker Build**
- Build Docker images for all services in parallel.
- Tag images with `<sha>-<timestamp>`.
- Scan built images for vulnerabilities.

**Stage 5 — Publish**
- Push images to private container registry.
- Update deployment manifests with new image tags (or use image tag promotion).

**Stage 6 — Deploy**
- **Staging** (automatic on `develop` merge): `kubectl apply` with new image tags. Wait for rollout. Run smoke tests.
- **Production** (automatic on `main` merge, after staging validation): Rolling update. Health check verification. Post-deploy smoke tests.

### Rollback

Rollback is a first-class operation:

1. **Automatic rollback:** If a new pod fails its readiness probe during a rolling update, Kubernetes halts the rollout. If `progressDeadlineSeconds` (default: 600s) is exceeded, the deployment status reports failure and CI alerts the team.

2. **Manual rollback:** Run `kubectl rollout undo deployment/<name>` to instantly revert to the previous ReplicaSet. This is a single command with zero downtime.

3. **Image-based rollback:** Re-deploy the previous known-good image tag. Update the deployment manifest to reference the older `<sha>` tag and apply.

4. **Database rollback:** If a migration is incompatible with the previous application version, run the corresponding down-migration before rolling back the application. Migrations are designed to be backward-compatible whenever possible (expand-then-contract pattern).

### CI/CD Sequence Diagram

```
Developer        GitHub          CI Runner        Registry      Kubernetes
    │                │                │                │              │
    │── push ───────▶│                │                │              │
    │                │── trigger ────▶│                │              │
    │                │                │── deps (py+js) │              │
    │                │                │── tsc          │              │
    │                │                │── jest         │              │
    │                │                │── lint         │              │
    │                │                │── npm audit    │              │
    │                │                │── trivy scan   │              │
    │                │                │                │              │
    │                │                │── docker build │              │
    │                │                │── docker push ▶│              │
    │                │                │                │              │
    │                │                │── kubectl     ─┼─────────────▶│
    │                │                │   apply        │              │
    │                │                │                │              │── rolling
    │                │                │                │              │   update
    │                │                │                │              │
    │                │                │◀── health ─────┼──────────────│
    │                │                │    check OK    │              │
    │                │                │                │              │
    │                │◀── status ─────│                │              │
    │◀── notify ─────│   (success)    │                │              │
    │                │                │                │              │
    │  [ON FAILURE]  │                │                │              │
    │                │                │◀── rollout ────┼──────────────│
    │                │                │    failed      │              │
    │                │                │── kubectl     ─┼─────────────▶│
    │                │                │   rollout undo │              │── rollback
    │                │◀── status ─────│                │              │
    │◀── alert ──────│   (failure)    │                │              │
    │                │                │                │              │
```

---

## 10. Configuration Management

### Environment Variables

All application configuration is externalized as environment variables. No configuration is hardcoded or embedded in container images.

**Application Configuration (ConfigMap):**

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | Backend runtime environment (`development`, `test`, `staging`, `production`). The frontend keeps `NODE_ENV`. |
| `PORT` | `8000` | Backend listen port |
| `WEB_PORT` | `3000` | Frontend listen port |
| `LOG_LEVEL` | `info` | Logging verbosity (`debug`, `info`, `warn`, `error`) |
| `LOG_FORMAT` | `json` | Log output format (`json` for production, `pretty` for dev) |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |
| `API_PREFIX` | `/api/v1` | API route prefix |
| `UPLOAD_MAX_SIZE_MB` | `100` | Maximum file upload size |
| `CELERY_CONCURRENCY_OCR` | `2` | OCR worker concurrent task limit per instance |
| `CELERY_CONCURRENCY_GENERATION` | `3` | Generation worker concurrent task limit per instance |
| `CELERY_CONCURRENCY_EXPORT` | `5` | Export worker concurrent task limit per instance |
| `JOB_MAX_RETRIES` | `3` | Maximum retry attempts before DLQ |
| `JOB_BACKOFF_TYPE` | `exponential` | Retry backoff strategy |
| `JOB_BACKOFF_DELAY` | `5000` | Initial retry delay in milliseconds |

**Sensitive Configuration (Secrets):**

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string with credentials |
| `REDIS_URL` | Redis connection string with password |
| `JWT_SECRET` | HMAC secret for access token signing |
| `JWT_REFRESH_SECRET` | HMAC secret for refresh token signing |
| `S3_ACCESS_KEY_ID` | Object storage access key |
| `S3_SECRET_ACCESS_KEY` | Object storage secret key |
| `S3_ENDPOINT` | Object storage endpoint URL |
| `S3_BUCKET_UPLOADS` | Bucket name for textbook uploads |
| `S3_BUCKET_EXPORTS` | Bucket name for paper exports |
| `GEMINI_API_KEY` | Gemini API key — the only AI provider credential (ADR-008) |

### Configuration Hierarchy

```
┌─────────────────────────────────────────────┐
│  Priority 1: Runtime Environment Variables   │  ◄── K8s Secrets + ConfigMaps
│  (Highest precedence)                        │      or Docker Compose .env
├─────────────────────────────────────────────┤
│  Priority 2: packages/config env schema      │  ◄── Validated with zod
│  (Typed defaults, runtime validation)        │      at application startup
├─────────────────────────────────────────────┤
│  Priority 3: Application code defaults       │  ◄── Fallback values in code
│  (Safe defaults for development)             │      for developer convenience
└─────────────────────────────────────────────┘
```

The `packages/config` module (as defined in `02_System_Architecture.md`) validates all environment variables at startup using a Pydantic `Settings` model. Missing required variables cause the application to fail fast with a clear error message before accepting traffic.

### Feature Flags

Feature flags control the rollout of new functionality without redeployment:

| Flag | Default | Description |
|------|---------|-------------|
| `FF_ENABLE_AI_GENERATION` | `true` | Master switch for AI generation (disabling falls back to retrieval-only) |
| `FF_ENABLE_BULK_EXPORT` | `false` | Enable bulk paper export (performance-intensive) |
| `FF_ENABLE_ADVANCED_ANALYTICS` | `false` | Enable advanced analytics dashboards |
| `FF_ENABLE_SSO` | `false` | Enable SSO authentication providers |
| `FF_MAX_CONCURRENT_GENERATIONS` | `5` | Per-tenant limit on concurrent generation jobs |

Feature flags are stored in ConfigMaps and can be updated with a `kubectl apply` without restarting pods (the application reloads configuration periodically or via a file watcher).

### Version Management

| Artifact | Versioning Scheme | Example |
|----------|-------------------|---------|
| Application code | Semantic versioning | `v1.2.3` |
| Docker images | Git SHA + timestamp | `api:a1b2c3d-20260730T1200Z` |
| Database migrations | Sequential timestamp | `20260730120000_add_usage_table` |
| API version | URL prefix | `/api/v1` |
| Prompt templates | Database-managed version | `prompt_versions.version = 3` |
| Kubernetes manifests | Git SHA (same repo) | Tracked in `infra/` directory |

---

## 11. Database Deployment

### PostgreSQL Configuration

| Parameter | Development | Staging | Production |
|-----------|-------------|---------|------------|
| **Version** | 16.x | 16.x | 16.x |
| **pgvector version** | 0.7.x | 0.7.x | 0.7.x |
| **Instance type** | Container (1 CPU, 1 GB) | Managed (2 CPU, 4 GB) | Managed (4 CPU, 16 GB) |
| **Storage** | 10 GB local volume | 50 GB SSD | 200 GB provisioned IOPS SSD |
| **Replication** | None | None | 1 synchronous + 1 async replica |
| **Connection limit** | 100 | 200 | 500 |
| **max_connections** | 100 | 200 | 500 |
| **shared_buffers** | 256 MB | 1 GB | 4 GB |
| **effective_cache_size** | 768 MB | 3 GB | 12 GB |
| **work_mem** | 4 MB | 16 MB | 32 MB |
| **maintenance_work_mem** | 64 MB | 256 MB | 1 GB |
| **wal_level** | replica | replica | replica |
| **archive_mode** | off | off | on |

### pgvector Extension

pgvector is installed in the same PostgreSQL instance. It stores embedding vectors for the question bank and textbook assets, enabling semantic similarity search during the retrieval pipeline.

**Index strategy for pgvector:**

| Index | Table | Column | Type | Parameters |
|-------|-------|--------|------|------------|
| `idx_question_embeddings_vector` | `question_embeddings` | `embedding` | IVFFlat | `lists = 100` (re-tune as row count grows) |
| `idx_textbook_asset_embeddings_vector` | `textbook_assets` | `embedding` | IVFFlat | `lists = 50` |

IVFFlat is chosen over HNSW for the initial deployment because it uses less memory and is faster to build. As the question bank grows beyond 1M rows, HNSW should be evaluated for higher recall at the cost of memory.

**Vector dimension:** **1536**, Matryoshka-truncated from the Gemini embedding model's native 3072 so the value stays inside pgvector's 2000-dimension HNSW/IVFFlat index limit (ADR-008). The width is recorded per row in `question_embeddings.dimensions`; changing it requires re-embedding all content, so it is treated as immutable once set.

### Database Migrations

Migrations are managed by **Alembic**, stored in `packages/db/alembic/versions/`. Every revision is forward-only in production. RLS policies, partitions, GIN/trigram indexes, generated `tsvector` columns, and (later) HNSW indexes are hand-written `op.execute(...)` SQL inside revisions, because Alembic autogenerate does not model them.

**Migration workflow:**

1. Developer modifies the SQLAlchemy models in `packages/db`.
2. Run `alembic revision --autogenerate` locally, then **hand-edit** the revision to add any RLS,
   partition, or index DDL that autogenerate cannot see.
3. The revision file is committed to version control and reviewed like code.
4. CI validates the revision applies cleanly against a fresh database and that its downgrade runs.
5. Staging deployment runs `alembic upgrade head` before application startup.
6. Production runs the migration as a **pre-deploy task** — an ECS one-off task at MVP, a Kubernetes
   Job at scale — gating the application rollout. A failed migration aborts the deploy.

**Migration safety rules:**
- **Never drop a column or table in the same release** that stops using it. Use the expand-then-contract pattern: release 1 adds the new column, release 2 migrates data, release 3 removes the old column.
- **All migrations must be idempotent.** Running a migration twice must not fail or corrupt data.
- **All migrations must be backward-compatible** with the currently running application version. This enables zero-downtime rolling deployments.
- **Large data migrations** (e.g., backfilling a new column for millions of rows) run as background jobs, not in the migration transaction.

### Connection Pooling

The backend uses a connection pool to manage PostgreSQL connections efficiently:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Pool size (per backend pod) | 10 | 3 pods × 10 = 30 active connections; well within the 500 limit |
| Pool size (per worker pod) | 5 | Workers are I/O-bound on external APIs, not database |
| Idle timeout | 30 seconds | Release unused connections promptly |
| Connection timeout | 5 seconds | Fail fast if pool is exhausted |
| Statement timeout | 30 seconds | Prevent runaway queries |

For higher scale, PgBouncer can be deployed as a sidecar or standalone pod to provide connection multiplexing. This is recommended when total pod count exceeds 20.

### Replication

```
┌──────────────────┐     WAL streaming     ┌──────────────────┐
│   Primary        │────────────────────▶  │ Sync Replica     │
│   (read/write)   │     (synchronous)     │ (read-only)      │
│                  │                       │ Same AZ or        │
│                  │                       │ adjacent AZ       │
└──────────────────┘                       └──────────────────┘
         │
         │ WAL streaming (asynchronous)
         ▼
┌──────────────────┐
│  Async Replica   │
│  (read-only)     │
│  Different AZ    │
│  Read scaling    │
└──────────────────┘
```

- **Synchronous replica:** Ensures zero data loss on primary failure. Slightly higher write latency (1–3 ms). Used for automatic failover.
- **Asynchronous replica:** Used for read scaling (analytics queries, reporting dashboards). Allows slight replication lag (typically < 1s).
- **Failover:** Managed service handles automatic promotion of the synchronous replica to primary. Application connection strings use a proxy or DNS endpoint that resolves to the current primary.

### Database Maintenance

| Task | Frequency | Method |
|------|-----------|--------|
| `VACUUM ANALYZE` | Daily (off-peak) | Managed service auto-vacuum or cron job |
| `REINDEX` (pgvector indexes) | Weekly | Scheduled maintenance window |
| Index usage review | Monthly | Query `pg_stat_user_indexes` |
| Bloat analysis | Monthly | `pgstattuple` extension |
| Query performance review | Weekly | `pg_stat_statements` analysis |
| Storage growth monitoring | Daily | Prometheus `pg_database_size` metric |

---

## 12. Redis Deployment

### Redis Architecture

Redis serves three distinct roles in the platform. All three roles run on the same Redis instance (or cluster) in the initial deployment, with logical separation via key prefixes and database numbers.

| Role | Key Prefix / DB | Data Type | TTL |
|------|-----------------|-----------|-----|
| **Cache** | `cache:*` (DB 0) | Strings, Hashes | 5m – 1h (per key type) |
| **Queues** | `celery:*` (DB 0) | Celery internal structures | Until processed |
| **Pub/Sub** | Channels: `progress:*`, `notification:*` | Messages | Ephemeral (not persisted) |

### Cache Strategy

| Cache Key Pattern | Data | TTL | Invalidation |
|-------------------|------|-----|--------------|
| `cache:session:<userId>` | User session data | 24 hours | On logout, password change |
| `cache:curriculum:<orgId>:<boardId>` | Curriculum tree | 1 hour | On curriculum version publish |
| `cache:ratelimit:<ip>:<endpoint>` | Request count | 1 minute (sliding window) | Automatic expiry |
| `cache:org:<orgId>:config` | Organization configuration | 30 minutes | On org config update |
| `cache:user:<userId>:permissions` | Resolved RBAC permissions | 15 minutes | On role change |

### Queue Configuration

| Queue | Concurrency | Max Retries | Backoff | Job Timeout | DLQ |
|-------|-------------|-------------|---------|-------------|-----|
| `ocr` | 2 per worker | 3 | Exponential (5s, 20s, 60s) | 10 minutes | `ocr:dlq` |
| `generation` | 3 per worker | 3 | Exponential (10s, 30s, 120s) | 5 minutes | `generation:dlq` |
| `export` | 5 per worker | 3 | Exponential (5s, 15s, 45s) | 3 minutes | `export:dlq` |
| `notification` | 10 per worker | 2 | Fixed (5s) | 30 seconds | `notification:dlq` |
| `embedding` | 5 per worker | 3 | Exponential (5s, 20s, 60s) | 2 minutes | `embedding:dlq` |

### Persistence Configuration

| Parameter | Development | Staging | Production |
|-----------|-------------|---------|------------|
| Persistence | None | RDB snapshots every 5 min | AOF (appendfsync everysec) + RDB hourly |
| maxmemory | 256 MB | 512 MB | 2 GB |
| maxmemory-policy | `allkeys-lru` | `allkeys-lru` | `volatile-lru` |

**Rationale for `volatile-lru` in production:** Queue data does not have TTLs and must not be evicted. Cache keys have TTLs and are safe to evict under memory pressure. `volatile-lru` evicts only keys with TTLs, protecting queue integrity.

### High Availability

```
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│ Redis Primary│──────▶│ Redis Replica│       │ Redis Replica│
│ (read/write) │  sync │ (read-only)  │       │ (read-only)  │
└──────────────┘       └──────────────┘       └──────────────┘
       │                      │                      │
       ▼                      ▼                      ▼
┌──────────────────────────────────────────────────────────┐
│                    Redis Sentinel                         │
│  • Monitors primary health                               │
│  • Promotes replica on primary failure                   │
│  • Notifies clients of new primary                       │
│  • Quorum: 2 of 3 sentinels must agree                  │
└──────────────────────────────────────────────────────────┘
```

Alternatively, if using a managed Redis service (AWS ElastiCache, Google Memorystore), the provider handles failover automatically with a single endpoint that resolves to the current primary.

### Redis Backup

| Backup Type | Frequency | Retention | Storage |
|-------------|-----------|-----------|---------|
| RDB snapshot | Hourly | 24 hours | Object storage (same region) |
| AOF file | Continuous | 7 days | Object storage |
| Full export | Daily | 30 days | Object storage (cross-region) |

---

## 13. Object Storage

### Bucket Organization

```
┌──────────────────────────────────────────────────┐
│                  Object Storage                    │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │  Bucket: aipaper-uploads                      │ │
│  │  Purpose: Raw textbook files uploaded by      │ │
│  │           teachers                             │ │
│  │  Access: Signed upload URLs (time-limited)    │ │
│  │  Lifecycle: Move to cold storage after 90d    │ │
│  └──────────────────────────────────────────────┘ │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │  Bucket: aipaper-processing                   │ │
│  │  Purpose: Intermediate processing artifacts   │ │
│  │           (extracted pages, OCR output)        │ │
│  │  Access: Workers only (service credentials)   │ │
│  │  Lifecycle: Auto-delete after 30 days         │ │
│  └──────────────────────────────────────────────┘ │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │  Bucket: aipaper-exports                      │ │
│  │  Purpose: Rendered paper exports (PDF, DOCX)  │ │
│  │  Access: Signed download URLs (time-limited)  │ │
│  │  Lifecycle: Retain for plan duration + 30d    │ │
│  └──────────────────────────────────────────────┘ │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │  Bucket: aipaper-templates                    │ │
│  │  Purpose: Paper layout templates, branding    │ │
│  │           assets (logos, headers)              │ │
│  │  Access: Backend read, Admin write            │ │
│  │  Lifecycle: No automatic deletion             │ │
│  └──────────────────────────────────────────────┘ │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │  Bucket: aipaper-backups                      │ │
│  │  Purpose: Database and Redis backups          │ │
│  │  Access: Backup service credentials only      │ │
│  │  Lifecycle: Tiered retention (see §20)        │ │
│  └──────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

### Tenant Isolation in Storage

Objects are organized by tenant using a prefix-based path hierarchy:

```
aipaper-uploads/
├── org_<org_id>/
│   ├── textbooks/
│   │   ├── <textbook_id>/
│   │   │   └── <original_filename>
│   │   └── ...
│   └── ...
└── ...

aipaper-exports/
├── org_<org_id>/
│   ├── papers/
│   │   ├── <paper_id>/
│   │   │   ├── paper_v1.pdf
│   │   │   └── paper_v1.docx
│   │   └── ...
│   └── ...
└── ...
```

**Isolation enforcement:**
- Signed URLs are generated with the full `org_<org_id>/` prefix path. A teacher in Org A cannot construct a valid URL to access Org B's files.
- Backend validates that the requesting user's `org_id` matches the path prefix before generating a signed URL.
- Bucket policies deny access without valid signed URLs or service credentials.

### Signed URLs

| Operation | URL Type | Expiry | Max Size |
|-----------|----------|--------|----------|
| Textbook upload | Presigned PUT | 15 minutes | 100 MB |
| Page image upload (worker) | Presigned PUT | 5 minutes | 10 MB |
| Export download | Presigned GET | 1 hour | — |
| Template asset download | Presigned GET | 4 hours | — |

**Upload flow:**
1. Teacher initiates upload in the frontend.
2. Frontend calls `POST /api/v1/textbooks` with file metadata.
3. Backend validates file type and size, creates a `textbooks` record, generates a presigned PUT URL.
4. Frontend uploads the file directly to object storage using the signed URL.
5. Frontend calls `POST /api/v1/textbooks/<id>/confirm` to notify the backend.
6. Backend enqueues the OCR job.

This pattern keeps large file data off the backend servers entirely.

### Lifecycle Policies

| Bucket | Rule | Action | After |
|--------|------|--------|-------|
| `aipaper-uploads` | Raw textbook files | Transition to Infrequent Access | 90 days |
| `aipaper-uploads` | Raw textbook files | Transition to Glacier/Archive | 365 days |
| `aipaper-processing` | Intermediate artifacts | Delete | 30 days |
| `aipaper-exports` | Rendered exports | Delete | Subscription duration + 30 days |
| `aipaper-backups` | Daily backups | Delete | 30 days |
| `aipaper-backups` | Weekly backups | Delete | 90 days |
| `aipaper-backups` | Monthly backups | Delete | 365 days |

### Encryption

- **At rest:** Server-side encryption (SSE-S3 or SSE-KMS) enabled on all buckets.
- **In transit:** HTTPS enforced for all object storage API calls. Bucket policy denies non-HTTPS requests.
- **Key management:** Managed encryption keys (provider-managed or customer-managed KMS keys for compliance).

### Versioning

Object versioning is enabled on `aipaper-uploads` and `aipaper-exports` to protect against accidental overwrites. Processing and backup buckets do not require versioning (processing artifacts are disposable; backups are write-once).

---

## 14. Worker Deployment

### Worker Architecture

Workers are stateless Python (Celery) processes that reuse the same repository and service layers as the FastAPI API (as specified in `02_System_Architecture.md`). They connect to Redis to consume jobs and to PostgreSQL to read/write data.

```
                    Redis Queues
                         │
          ┌──────────────┼──────────────────┐
          │              │                  │
          ▼              ▼                  ▼
   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
   │  OCR Worker  │ │  Generation  │ │   Export     │
   │  Deployment  │ │  Worker      │ │   Worker     │
   │              │ │  Deployment  │ │  Deployment  │
   │  Consumes:   │ │              │ │              │
   │  • ocr queue │ │  Consumes:   │ │  Consumes:   │
   │              │ │  • generation│ │  • export    │
   │  Writes:     │ │    queue     │ │    queue     │
   │  • PostgreSQL│ │              │ │              │
   │  • Object    │ │  Calls:      │ │  Reads:      │
   │    Storage   │ │  • AI APIs   │ │  • PostgreSQL│
   │              │ │  • PostgreSQL│ │  • Templates │
   │  Publishes:  │ │              │ │              │
   │  • progress  │ │  Publishes:  │ │  Writes:     │
   │    events    │ │  • progress  │ │  • Object    │
   │              │ │    events    │ │    Storage   │
   └──────────────┘ └──────────────┘ └──────────────┘
```

### Worker Details

| Worker | Queue | Concurrency | Timeout | Resources | Scaling Trigger |
|--------|-------|-------------|---------|-----------|-----------------|
| **OCR Worker** | `ocr` | 2 per pod | 10 min | 1 CPU, 1 GB RAM | Queue depth > 50 |
| **Generation Worker** | `generation` | 3 per pod | 5 min | 0.5 CPU, 512 MB RAM | Queue depth > 20 |
| **Export Worker** | `export` | 5 per pod | 3 min | 0.5 CPU, 1 GB RAM | Queue depth > 30 |
| **Notification Worker** | `notification` (shared with backend) | 10 per pod | 30 sec | 0.25 CPU, 256 MB RAM | Queue depth > 100 |

### Queue Assignment

Each worker type consumes from exactly one primary queue. This ensures that a slow OCR pipeline does not block export processing.

```
┌───────────────────────────────────────────────────┐
│                   Redis                            │
│                                                    │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │celery:ocr│  │celery:generat.│  │celery:export│ │
│  │  waiting  │  │   waiting    │  │   waiting   │ │
│  │  active   │  │   active     │  │   active    │ │
│  │  delayed  │  │   delayed    │  │   delayed   │ │
│  │  failed   │  │   failed     │  │   failed    │ │
│  └─────┬────┘  └──────┬───────┘  └──────┬──────┘ │
│        │               │                 │        │
│  ┌─────▼────┐  ┌──────▼───────┐  ┌──────▼──────┐ │
│  │ ocr:dlq  │  │generation:dlq│  │ export:dlq  │ │
│  └──────────┘  └──────────────┘  └─────────────┘ │
└───────────────────────────────────────────────────┘
```

### Failure Recovery

**Job retry flow:**

1. Worker processes a job.
2. If the job throws an error:
   a. Celery increments the attempt counter.
   b. If `attempts < maxRetries`, the job is re-queued with exponential backoff delay.
   c. If `attempts >= maxRetries`, the job is moved to the dead-letter queue (DLQ).
3. A Prometheus counter `celery_task_failed_total{queue, reason}` is incremented.
4. If DLQ depth exceeds threshold (e.g., > 10), an alert fires.

**Dead-letter queue (DLQ) handling:**

- DLQ jobs are inspectable via an admin endpoint (`GET /api/v1/admin/queues/<name>/dlq`).
- Operators can retry DLQ jobs individually or in bulk after fixing the underlying issue.
- DLQ jobs carry the full job payload, error message, stack trace, and attempt history for debugging.

**Graceful shutdown:**

When a worker pod receives `SIGTERM` (during deployment or scale-down):
1. Stop accepting new jobs from the queue.
2. Wait for currently active jobs to complete (up to `terminationGracePeriodSeconds`).
3. If a job exceeds the grace period, it is returned to the queue as "stalled" and another worker picks it up.

### Worker Job Lifecycle

```
Job Created ──▶ Waiting ──▶ Active ──▶ Completed
                              │
                              │ (error)
                              ▼
                          Failed (attempt N)
                              │
                    ┌─────────┼─────────┐
                    │ N < max │         │ N ≥ max
                    ▼         │         ▼
              Delayed         │    Dead Letter Queue
              (backoff)       │    (manual intervention)
                    │         │
                    └─────────┘
                         │
                         ▼
                      Waiting
                    (retry)
```

---

## 15. AI Provider Deployment

### Provider Abstraction

The AI Service Layer (detailed in `06_AI_Architecture.md`) uses a provider-agnostic adapter pattern. The deployment architecture configures which providers are active and how they are accessed.

```
┌──────────────────────────────────────────────────┐
│               AI Service Layer                    │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │              Provider Registry                │ │
│  │                                                │ │
│  │            ┌───────────────────┐            │ │
│  │            │  Gemini Adapter   │            │ │
│  │            │  (the only one    │            │ │
│  │            │   implemented)    │            │ │
│  │            └─────────┬─────────┘            │ │
│  │                      │                       │ │
│  │  ┌───────────────────▼────────────────────┐  │ │
│  │  │         Circuit Breaker Layer           │  │ │
│  │  │  • Per-provider circuit breaker         │  │ │
│  │  │  • Failure threshold: 5 in 60s          │  │ │
│  │  │  • Recovery timeout: 120s               │  │ │
│  │  └────────────────────────────────────────┘  │ │
│  └──────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
   ┌──────────┐        ┌──────────┐
   │ LLM API  │        │Embedding │
   │ (HTTPS)  │        │ API      │
   └──────────┘        │ (HTTPS)  │
                       └──────────┘
```

### API Key Management

| Provider | Key Variable | Rotation Frequency | Storage |
|----------|-------------|-------------------|---------|
| Google Gemini (generation, classification, embeddings) | `GEMINI_API_KEY` | 90 days | Secrets Manager (Fargate) / Kubernetes Secret (at scale) |
| Mathpix (OCR) | `MATHPIX_APP_KEY` | 90 days | Secrets Manager / Kubernetes Secret |

Keys are:
- Never committed to version control.
- Never logged or included in error messages.
- Rotated on a schedule (and immediately on suspected compromise).
- Different keys for staging and production.

### Rate Limiting

| Provider | Rate Limit (RPM) | Token Limit (TPM) | Platform Enforcement |
|----------|------------------|--------------------|-----------------------|
| Gemini (Pro tier) | Per-tier (varies) | Per-tier (varies) | Token bucket limiter in AI Service Layer |
| Gemini (Flash tier) | Per-tier (varies) | Per-tier (varies) | Token bucket limiter in AI Service Layer |

The platform enforces its own rate limits on top of provider limits to:
- Prevent a single tenant's bulk generation from exhausting the platform's API quota.
- Distribute quota fairly across tenants.
- Leave headroom for retries.

Per-tenant rate limits are configurable via the billing module's plan tier (as defined in `01_Product_Requirements.md`).

### Retry Strategy

| Scenario | Retry | Backoff | Max Attempts | Fallback |
|----------|-------|---------|--------------|----------|
| Rate limit (429) | Yes | Respect `Retry-After` header | 5 | Queue for later processing |
| Server error (500+) | Yes | Exponential (2s, 4s, 8s, 16s) | 3 | Try fallback provider |
| Timeout | Yes | Exponential (5s, 10s, 20s) | 3 | Try fallback provider |
| Auth error (401/403) | No | — | 1 | Alert operations, fail job |
| Invalid request (400) | No | — | 1 | Fail job, log for debugging |

### Fallback Providers

The provider registry supports a fallback chain. If the primary provider for an operation fails (circuit breaker open), the system attempts the next provider in the chain:

```
Generation:     Gemini Pro tier   -> retry w/ backoff -> retrieval fill -> unmet slot (AI-3)
Classification: Gemini Flash tier -> deterministic rule fallback -> flag for review
Embedding:      Gemini embedding  -> re-queue for later (question stored without a vector)
Budget/outage:  DEGRADE — retrieval-only paper with honest gaps, never a hard failure
```

Fallback is transparent to the calling service. The AI Service Layer logs which provider ultimately served each request for cost tracking and quality monitoring.

### Timeout Configuration

| Operation | Timeout | Rationale |
|-----------|---------|-----------|
| Single question generation | 30 seconds | LLM response for one question with grounding |
| Batch generation (5 questions) | 120 seconds | Multiple sequential LLM calls |
| Embedding (single text) | 10 seconds | Fast vector computation |
| Embedding (batch of 50) | 60 seconds | Batch API call |
| Validation (single question) | 15 seconds | Quick classification check |

### AI Provider Monitoring

| Metric | Alert Threshold | Action |
|--------|----------------|--------|
| `ai_request_latency_p99` | > 10s sustained for 5 min | Page on-call; check provider status page |
| `ai_request_error_rate` | > 10% in 5-min window | Alert; circuit breaker may trip automatically |
| `ai_token_usage_daily` | > 80% of daily budget | Warning; notify finance and engineering leads |
| `ai_circuit_breaker_open` | Any provider open | Alert; automatic fallback active |
| `ai_provider_cost_daily` | > daily budget threshold | Warning; consider throttling non-critical generation |

---

## 16. Scaling Strategy

### Scaling Summary

```
┌──────────────────────────────────────────────────────────────────┐
│                      SCALING ARCHITECTURE                         │
│                                                                    │
│  ┌─────────────┐                                                  │
│  │   Frontend   │  Horizontal (HPA: CPU > 70%)                   │
│  │   3 → 10     │  Stateless, fast scale-up                      │
│  └─────────────┘                                                  │
│                                                                    │
│  ┌─────────────┐                                                  │
│  │   Backend    │  Horizontal (HPA: CPU > 70% OR p99 > 500ms)    │
│  │   3 → 15    │  Stateless, fast scale-up                       │
│  └─────────────┘                                                  │
│                                                                    │
│  ┌─────────────┐                                                  │
│  │   Workers    │  Horizontal (HPA: custom queue depth metric)    │
│  │   1 → 6-8   │  Per-worker-type, independent scaling           │
│  └─────────────┘                                                  │
│                                                                    │
│  ┌─────────────┐                                                  │
│  │  PostgreSQL  │  Vertical (tier upgrade) + Read replicas        │
│  │              │  Managed service handles failover               │
│  └─────────────┘                                                  │
│                                                                    │
│  ┌─────────────┐                                                  │
│  │    Redis     │  Vertical (memory upgrade)                      │
│  │              │  Redis Cluster for extreme scale                │
│  └─────────────┘                                                  │
│                                                                    │
│  ┌─────────────┐                                                  │
│  │   Object    │  Managed service, effectively unlimited          │
│  │   Storage   │  No scaling action required                      │
│  └─────────────┘                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Frontend Scaling

| Trigger | Scale Direction | Replicas | Cooldown |
|---------|----------------|----------|----------|
| CPU ≥ 70% | Scale up | +1 (max 10) | 60 seconds |
| CPU ≤ 30% for 5 min | Scale down | -1 (min 3) | 300 seconds |

Frontend pods are lightweight (SSR + static serving) and scale quickly. The bottleneck is rarely the frontend — it's typically the backend or workers.

### Backend Scaling

| Trigger | Scale Direction | Replicas | Cooldown |
|---------|----------------|----------|----------|
| CPU ≥ 70% | Scale up | +1 (max 15) | 60 seconds |
| Request latency p99 ≥ 500ms | Scale up | +1 (max 15) | 30 seconds |
| CPU ≤ 30% for 5 min AND p99 ≤ 200ms | Scale down | -1 (min 3) | 300 seconds |

**Connection pool scaling consideration:** Each backend pod opens `POOL_SIZE` connections to PostgreSQL. When scaling from 3 to 15 pods at pool size 10, total connections reach 150. This is well within the PostgreSQL `max_connections` limit of 500 but should be monitored. If pod count consistently exceeds 20, deploy PgBouncer.

### Worker Scaling

Workers scale based on queue depth, a custom metric exposed by the Celery exporter and scraped by Prometheus:

| Worker | Scale-up Trigger | Scale-down Trigger | Min | Max |
|--------|-----------------|-------------------|-----|-----|
| OCR | Queue waiting > 50 | Queue waiting = 0 for 10 min | 1 | 8 |
| Generation | Queue waiting > 20 | Queue waiting = 0 for 10 min | 1 | 6 |
| Export | Queue waiting > 30 | Queue waiting = 0 for 10 min | 1 | 6 |

**Burst scaling:** During bulk operations (start-of-term textbook uploads, exam-period paper generation), queue depth can spike rapidly. The HPA `behavior.scaleUp.stabilizationWindowSeconds` is set to 30 seconds for workers, allowing fast reaction to queue pressure.

### Database Scaling

PostgreSQL scaling is primarily vertical (managed service tier upgrade) with read replicas for horizontal read scaling:

| Scale Action | When | Impact |
|-------------|------|--------|
| Vertical upgrade (CPU/RAM) | CPU > 80% sustained, connection saturation | Brief downtime during resize (managed service handles) |
| Add read replica | Read query volume > 60% of capacity | Zero downtime; configure read-only connection string |
| Increase IOPS | Disk I/O wait > 20% | Zero downtime (managed service) |
| Increase storage | Storage > 75% | Zero downtime (managed service, auto-expand) |

### Load Balancing

| Layer | Algorithm | Health Check | Session Affinity |
|-------|-----------|-------------|-----------------|
| Cloud Load Balancer | Round Robin | HTTP GET `/api/health` every 10s | None |
| Nginx Ingress | Round Robin (default) | Kubernetes readiness probes | None |
| Redis Sentinel | N/A (single primary) | Sentinel monitoring | N/A |
| PostgreSQL | N/A (single primary for writes) | Managed health checks | N/A |

### Auto-scaling Rules Table

| Component | Metric Source | Metric | Target | Min Replicas | Max Replicas |
|-----------|-------------|--------|--------|-------------|-------------|
| Frontend | Kubernetes metrics-server | CPU | 70% | 3 | 10 |
| Backend | Kubernetes metrics-server | CPU | 70% | 3 | 15 |
| Backend | Prometheus adapter | http_request_duration_p99 | 500ms | 3 | 15 |
| Worker-OCR | Prometheus adapter (KEDA) | celery_queue_waiting{queue="ocr"} | 50 | 1 | 8 |
| Worker-Gen | Prometheus adapter (KEDA) | celery_queue_waiting{queue="generation"} | 20 | 1 | 6 |
| Worker-Export | Prometheus adapter (KEDA) | celery_queue_waiting{queue="export"} | 30 | 1 | 6 |

---

## 17. Security

### TLS Configuration

| Aspect | Configuration |
|--------|--------------|
| Minimum version | TLS 1.2 |
| Preferred version | TLS 1.3 |
| Cipher suites | ECDHE-ECDSA-AES256-GCM-SHA384, ECDHE-RSA-AES256-GCM-SHA384 |
| Certificate management | cert-manager with Let's Encrypt |
| Certificate renewal | Automatic, 30 days before expiry |
| HSTS | `max-age=31536000; includeSubDomains; preload` |

### Secrets Management

```
┌──────────────────────────────────────────────────────────────┐
│                    SECRETS LIFECYCLE                           │
│                                                                │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐   │
│  │   Create     │───▶│    Store     │───▶│    Inject     │   │
│  │              │    │              │    │               │   │
│  │ Generate     │    │ Sealed       │    │ K8s Secret    │   │
│  │ strong keys  │    │ Secrets or   │    │ mounted as    │   │
│  │ or passwords │    │ External     │    │ env vars in   │   │
│  │              │    │ Vault        │    │ pods          │   │
│  └──────────────┘    └──────────────┘    └───────────────┘   │
│                                                ▼              │
│                                          ┌──────────────┐    │
│  ┌──────────────┐                        │   Rotate     │    │
│  │   Audit      │◀───────────────────────│              │    │
│  │              │                        │ On schedule  │    │
│  │ Log access   │                        │ or breach    │    │
│  │ to secrets   │                        │              │    │
│  └──────────────┘                        └──────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

**Rules:**
- Secrets are encrypted at rest in etcd (Kubernetes cluster encryption).
- Secrets are never printed in logs (the logging interceptor masks sensitive fields).
- Secrets are never included in error responses.
- Secrets are never committed to version control (`.gitignore` enforced, pre-commit hook scans).
- Secret rotation does not require pod restart (application watches mounted secret files or supports dynamic reload).

### JWT Security

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Access token algorithm | HS256 or RS256 | HS256 for simplicity; RS256 for zero-trust between services |
| Access token expiry | 15 minutes | Short-lived to limit exposure window |
| Refresh token expiry | 7 days | Convenience for users; stored in httpOnly cookie |
| Token storage (client) | httpOnly, Secure, SameSite=Strict cookie | Not accessible to JavaScript (XSS-resistant) |
| Token revocation | Redis blacklist (on logout, password change) | Immediate invalidation without waiting for expiry |

### Network Isolation

- **VPC-level:** All cluster nodes run in a private VPC. No public IP addresses on nodes.
- **Namespace-level:** Network policies deny all cross-namespace traffic by default. Explicit allow rules open only required paths.
- **Pod-level:** Worker pods cannot receive inbound connections. Backend pods reject connections from worker namespace.

### Container Security

| Measure | Implementation |
|---------|---------------|
| Non-root execution | All containers run as a non-root UID (`app` in Python images, `node` in the frontend image) |
| Read-only root filesystem | `readOnlyRootFilesystem: true` in security context |
| No privilege escalation | `allowPrivilegeEscalation: false` |
| Drop capabilities | `drop: ["ALL"]` |
| Resource limits | CPU and memory limits enforced on all pods |
| Image scanning | Trivy scan in CI; admission webhook rejects unscanned images |
| Base image updates | Dependabot or Renovate alerts for base image CVEs |

### Security Headers

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://storage.example.com; connect-src 'self' https://storage.example.com
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

### CORS Configuration

```
Access-Control-Allow-Origin: https://app.example.com
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization, X-Request-ID
Access-Control-Allow-Credentials: true
Access-Control-Max-Age: 86400
```

### Rate Limiting

| Endpoint | Limit | Window | Action on Exceed |
|----------|-------|--------|-----------------|
| `POST /auth/login` | 10 requests | 1 minute | 429 Too Many Requests |
| `POST /auth/password-reset` | 3 requests | 1 hour | 429 |
| `POST /api/v1/*` (write endpoints) | 100 requests | 1 minute | 429 |
| `GET /api/v1/*` (read endpoints) | 500 requests | 1 minute | 429 |
| `POST /api/v1/textbooks` (upload) | 10 requests | 1 hour | 429 |
| `POST /api/v1/papers/generate` | 20 requests | 1 hour | 429 |

Rate limits are per-user and per-IP, tracked in Redis with sliding window counters.

---

## 18. Monitoring & Observability

### Three Pillars Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    OBSERVABILITY STACK                             │
│                                                                    │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│  │   METRICS   │    │    LOGS     │    │   TRACES    │          │
│  │             │    │             │    │             │          │
│  │ Prometheus  │    │ Loki / ELK  │    │ Jaeger /    │          │
│  │             │    │             │    │ Tempo       │          │
│  │ • App       │    │ • Structured│    │             │          │
│  │   metrics   │    │   JSON logs │    │ • Request   │          │
│  │ • System    │    │ • Request   │    │   tracing   │          │
│  │   metrics   │    │   logs      │    │ • Cross-    │          │
│  │ • Custom    │    │ • Audit     │    │   service   │          │
│  │   business  │    │   logs      │    │   spans     │          │
│  │   metrics   │    │ • Error     │    │ • External  │          │
│  │             │    │   logs      │    │   API spans │          │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘          │
│         │                  │                  │                   │
│         └──────────────────┼──────────────────┘                   │
│                            ▼                                      │
│                    ┌─────────────┐                                │
│                    │   Grafana   │                                │
│                    │  Dashboards │                                │
│                    └──────┬──────┘                                │
│                           │                                       │
│                    ┌──────▼──────┐                                │
│                    │ Alertmanager│                                │
│                    │             │                                │
│                    │ → PagerDuty │                                │
│                    │ → Slack     │                                │
│                    │ → Email     │                                │
│                    └─────────────┘                                │
└──────────────────────────────────────────────────────────────────┘
```

### Key Metrics

| Category | Metric | Source | Alert Threshold |
|----------|--------|--------|-----------------|
| **API** | `http_requests_total` | Backend | N/A (dashboard only) |
| **API** | `http_request_duration_seconds` (p50, p95, p99) | Backend | p99 > 2s for 5 min |
| **API** | `http_request_errors_total` (5xx) | Backend | Rate > 5% for 3 min |
| **Queue** | `celery_queue_waiting{queue}` | Celery exporter | OCR > 200, Gen > 100, Export > 100 |
| **Queue** | `celery_queue_active{queue}` | Celery exporter | N/A (dashboard only) |
| **Queue** | `celery_task_duration_seconds{queue}` | Celery exporter | p99 > 2x expected |
| **Queue** | `celery_dlq_depth{queue}` | Celery exporter | Any > 10 |
| **Database** | `pg_active_connections` | PostgreSQL exporter | > 80% of max_connections |
| **Database** | `pg_replication_lag_seconds` | PostgreSQL exporter | > 5 seconds |
| **Database** | `pg_database_size_bytes` | PostgreSQL exporter | > 80% of provisioned storage |
| **Database** | `pg_slow_queries_total` | PostgreSQL exporter | > 50 per 5 min |
| **Redis** | `redis_memory_used_bytes` | Redis exporter | > 80% of maxmemory |
| **Redis** | `redis_connected_clients` | Redis exporter | > 500 |
| **Redis** | `redis_keyspace_misses_total` | Redis exporter | Miss rate > 30% |
| **AI** | `ai_request_total{tier, operation}` | Backend | N/A (cost tracking) |
| **AI** | `ai_request_duration_seconds{tier}` | Backend | p99 > 15s |
| **AI** | `ai_token_usage_total{tier}` | Backend | Daily > 80% budget |
| **AI** | `ai_circuit_breaker_state{provider}` | Backend | Any = open |
| **AI** | `ai_cascade_rung_total{rung}` | Backend | Rising share of rungs 5-6 (see `06_AI_Architecture.md` 18.1.1) |
| **System** | `node_cpu_utilization` | Node exporter | > 85% for 10 min |
| **System** | `node_memory_utilization` | Node exporter | > 85% for 10 min |
| **System** | `node_disk_utilization` | Node exporter | > 85% |
| **App** | `active_tenants_total` | Backend | N/A (business metric) |
| **App** | `papers_generated_total` | Backend | N/A (business metric) |
| **App** | `textbooks_processed_total` | Backend | N/A (business metric) |

### Grafana Dashboards

| Dashboard | Purpose | Key Panels |
|-----------|---------|------------|
| **Platform Overview** | High-level health at a glance | Request rate, error rate, latency p99, queue depths, active users |
| **API Performance** | Backend request analysis | Per-endpoint latency, error rates, request volume, slow queries |
| **Worker Health** | Queue and worker monitoring | Queue depth over time, job processing rate, failure rate, DLQ depth |
| **Database** | PostgreSQL monitoring | Connections, replication lag, query duration, disk I/O, cache hit ratio |
| **Redis** | Cache and queue monitoring | Memory usage, hit/miss ratio, queue lengths, pub/sub channels |
| **AI Providers** | External AI service monitoring | Per-provider latency, error rate, token usage, cost projection |
| **Tenant Activity** | Per-tenant usage analytics | Papers generated, textbooks uploaded, API calls, storage used |
| **Infrastructure** | Node and cluster health | Node CPU/memory/disk, pod status, restart count, OOM kills |

### Health Checks

| Endpoint | Component | Checks | Used By |
|----------|-----------|--------|---------|
| `GET /api/health` | Backend | Application up | Load Balancer, Ingress |
| `GET /api/health/ready` | Backend | DB connected, Redis connected, migrations applied | Kubernetes readiness probe |
| `GET /api/health/live` | Backend | Process running, event loop responsive | Kubernetes liveness probe |
| `GET /health` | Frontend | Next.js server running | Kubernetes readiness/liveness probe |
| Worker health | Workers | Queue connection active, processing jobs | Kubernetes liveness probe (custom) |

**Readiness probe configuration:**
- Initial delay: 10 seconds (allow startup time)
- Period: 10 seconds
- Failure threshold: 3 (mark unready after 3 consecutive failures)
- Success threshold: 1 (mark ready after 1 success)

**Liveness probe configuration:**
- Initial delay: 30 seconds
- Period: 30 seconds
- Failure threshold: 3 (restart after 3 consecutive failures)

---

## 19. Logging

### Structured Logging Format

All application logs are emitted as JSON to stdout. The container runtime (Docker/containerd) captures stdout and forwards it to the log aggregation pipeline.

**Standard log fields:**

```json
{
  "timestamp": "2026-07-30T12:00:00.000Z",
  "level": "info",
  "service": "api",
  "requestId": "req_a1b2c3d4",
  "correlationId": "cor_x9y8z7",
  "traceId": "trace_123abc",
  "tenantId": "org_456def",
  "userId": "usr_789ghi",
  "method": "POST",
  "path": "/api/v1/papers/generate",
  "statusCode": 201,
  "duration": 1234,
  "message": "Paper generation initiated",
  "metadata": {
    "paperId": "paper_abc123",
    "blueprintSlots": 25
  }
}
```

### Request ID and Correlation ID

- **Request ID (`x-request-id`):** Generated by Nginx Ingress for every incoming request. Propagated through all internal calls. If the client sends a request ID, it is accepted (up to 64 chars); otherwise, one is generated.
- **Correlation ID (`x-correlation-id`):** Groups related requests that form a user workflow. For example, a paper generation flow involves an API request, multiple worker jobs, and AI provider calls — all sharing the same correlation ID.
- **Trace ID:** OpenTelemetry trace identifier for distributed tracing. Correlates with Jaeger spans.

```
Browser Request
  │ x-request-id: req_001
  │ x-correlation-id: cor_abc
  ▼
Frontend (log: requestId=req_001, correlationId=cor_abc)
  │
  ▼
Backend (log: requestId=req_001, correlationId=cor_abc)
  │── enqueue job (jobId=job_xyz, correlationId=cor_abc)
  ▼
Worker (log: jobId=job_xyz, correlationId=cor_abc)
  │── call AI API (correlationId=cor_abc)
  ▼
AI Provider response (logged with correlationId=cor_abc)
```

### Log Levels

| Level | When Used | Examples |
|-------|-----------|---------|
| `error` | Unrecoverable failures, unhandled exceptions | Database connection lost, unhandled promise rejection |
| `warn` | Recoverable problems, degraded functionality | AI provider timeout (will retry), rate limit approached |
| `info` | Significant business events, request lifecycle | Paper generated, textbook uploaded, user logged in |
| `debug` | Detailed operational data (dev/staging only) | SQL queries, Redis commands, function entry/exit |

### Log Retention

| Log Type | Hot Storage (Loki/ES) | Cold Storage (S3) | Total Retention |
|----------|----------------------|--------------------|--------------------|
| Application logs | 14 days | 90 days | 90 days |
| Audit logs | 30 days | 365 days | 365 days |
| Access logs (Nginx) | 7 days | 30 days | 30 days |
| Error logs | 30 days | 180 days | 180 days |

### Audit Logs

Audit logs are a special category of structured logs that record all security-relevant and business-critical events. They are stored both in the `audit_logs` database table (as defined in `03_Database_Design.md`) and in the log aggregation pipeline.

Audit-logged events:
- User authentication (login, logout, failed login).
- Role changes (membership role assigned, revoked).
- Data mutations (question created, paper generated, textbook uploaded).
- AI operations (generation triggered, prompt version promoted).
- Administrative actions (organization created, plan changed).
- Secret access and rotation events.

### Sensitive Data Masking

The logging interceptor automatically masks sensitive fields before log emission:

| Field Pattern | Masking |
|---------------|---------|
| `password`, `secret`, `token` | Replace with `[REDACTED]` |
| `email` | `j***@example.com` |
| `apiKey` | `sk-***...***xyz` (first 3 + last 3 chars) |
| `creditCard` | Never logged |
| Request/response bodies over 10 KB | Truncated with `[TRUNCATED: 15234 bytes]` |

---

## 20. Backup Strategy

### Backup Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      BACKUP ARCHITECTURE                      │
│                                                                │
│  ┌────────────────┐     ┌───────────────────────────────┐    │
│  │   PostgreSQL   │────▶│  Continuous WAL Archiving     │    │
│  │   Primary      │     │  → Object Storage (same region)│    │
│  │                │     │  RPO: ~5 minutes               │    │
│  │                │     └───────────────────────────────┘    │
│  │                │                                          │
│  │                │────▶│  Daily pg_dump (full logical)  │    │
│  │                │     │  → Object Storage (cross-region)│   │
│  └────────────────┘     └───────────────────────────────┘    │
│                                                                │
│  ┌────────────────┐     ┌───────────────────────────────┐    │
│  │     Redis      │────▶│  Hourly RDB Snapshot          │    │
│  │                │     │  → Object Storage (same region)│    │
│  └────────────────┘     └───────────────────────────────┘    │
│                                                                │
│  ┌────────────────┐     ┌───────────────────────────────┐    │
│  │ Object Storage │────▶│  Cross-region replication      │    │
│  │ (Primary)      │     │  (provider-managed)            │    │
│  └────────────────┘     └───────────────────────────────┘    │
│                                                                │
│  ┌────────────────┐     ┌───────────────────────────────┐    │
│  │ K8s Manifests  │────▶│  Git repository               │    │
│  │ + Configs      │     │  (version controlled)          │    │
│  └────────────────┘     └───────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

### Database Backup Schedule

| Backup Type | Method | Frequency | Retention | Storage | Encryption |
|-------------|--------|-----------|-----------|---------|------------|
| Continuous WAL | WAL archiving (pg_receivewal) | Continuous | 7 days | Same-region S3 | AES-256 |
| Full logical backup | `pg_dump --format=custom` | Daily (02:00 UTC) | 30 days | Cross-region S3 | AES-256 |
| Weekly snapshot | Cloud provider snapshot | Weekly (Sunday 03:00 UTC) | 90 days | Provider-managed | Provider-managed |
| Monthly archive | `pg_dump` + compress | Monthly (1st, 04:00 UTC) | 365 days | Archive storage | AES-256 + GPG |

### Redis Backup Schedule

| Backup Type | Method | Frequency | Retention | Storage |
|-------------|--------|-----------|-----------|---------|
| RDB snapshot | `BGSAVE` | Hourly | 24 hours | Same-region S3 |
| AOF backup | Copy AOF file | Daily | 7 days | Same-region S3 |
| Full export | `redis-cli --rdb` | Weekly | 30 days | Cross-region S3 |

### Object Storage Backup

Primary object storage uses provider-managed cross-region replication. No separate backup process is needed for object storage — the replication provides both durability and geographic redundancy.

| Bucket | Replication | Versioning | Cross-Region Copy |
|--------|-------------|------------|-------------------|
| `aipaper-uploads` | Provider-managed | Enabled | Yes (secondary region) |
| `aipaper-exports` | Provider-managed | Enabled | Yes (secondary region) |
| `aipaper-processing` | None | Disabled | No (disposable data) |
| `aipaper-templates` | Provider-managed | Enabled | Yes (secondary region) |
| `aipaper-backups` | None | Disabled | Data is already the backup |

### Backup Verification

| Verification | Frequency | Method |
|-------------|-----------|--------|
| Restore test (DB) | Weekly | Restore latest backup to ephemeral instance, run integrity checks |
| Restore test (Redis) | Monthly | Restore RDB to ephemeral instance, verify queue structure |
| WAL replay test | Monthly | Point-in-time recovery to ephemeral instance |
| Cross-region restore test | Quarterly | Restore from secondary region, full platform boot |
| Backup integrity check | Daily | Verify checksums of all backup files |

### Recovery Testing

Every quarter, the operations team performs a full disaster recovery drill:

1. Provision a fresh Kubernetes cluster in the secondary region.
2. Restore PostgreSQL from the latest cross-region backup.
3. Restore Redis from the latest cross-region RDB.
4. Deploy all application services from the container registry.
5. Verify data integrity: run a suite of read queries and compare results.
6. Verify functionality: run smoke tests against the recovered platform.
7. Measure actual RTO and RPO. Document gaps.
8. Tear down the drill environment.

---

## 21. Disaster Recovery

### Failure Scenarios

| Scenario | Severity | Detection | Recovery |
|----------|----------|-----------|----------|
| **Single pod crash** | Low | Kubernetes liveness probe, auto-restart | Automatic: Kubernetes restarts the pod. No operator action. |
| **Node failure** | Medium | Kubernetes node health, pod rescheduling | Automatic: Pods rescheduled to healthy nodes. < 2 min downtime for affected pods. |
| **Database primary failure** | High | Managed service monitoring, replication lag spike | Semi-automatic: Managed service promotes sync replica. Application reconnects via proxy endpoint. < 30s write outage. |
| **Redis primary failure** | High | Sentinel monitoring | Semi-automatic: Sentinel promotes replica. Celery reconnects. < 15s queue interruption. In-flight jobs may need reprocessing. |
| **Object storage outage** | Medium | HTTPS error rate spike on storage calls | Manual: Wait for provider recovery. Uploads and downloads fail. Processing pipeline stalls. Core API remains functional. |
| **AI provider outage** | Medium | Circuit breaker trips, error rate alert | Automatic: Fallback to secondary provider. If all providers down, generation pauses. Retrieval-only mode activates. |
| **Full cluster failure** | Critical | Monitoring unreachable, external health check fails | Manual: Provision new cluster, restore from backups, deploy from registry. RTO ≤ 4 hours. |
| **Region failure** | Critical | External monitoring (multi-region) | Manual: Failover to secondary region. DNS switch. Database restore from cross-region backup. RTO ≤ 4 hours. RPO ≤ 1 hour. |
| **Data corruption** | Critical | Data integrity checks, user reports | Manual: Point-in-time recovery from WAL. Identify corruption window. Selective restore. |
| **Security breach** | Critical | IDS alerts, audit log anomalies | Manual: Isolate affected components. Rotate all secrets. Forensic analysis. Restore from pre-breach backup if needed. |

### Recovery Procedures

**Database Recovery (Primary Failure):**

1. Managed service detects primary unresponsive.
2. Synchronous replica is promoted to primary (automatic, < 30s).
3. Connection proxy updates to point to new primary (automatic).
4. Application reconnects (automatic, connection pool handles).
5. Old primary is replaced with a new replica (automatic, managed service).
6. Verify replication is healthy. Verify data integrity.

**Database Recovery (Full Failure, Backup Restore):**

1. Provision new PostgreSQL instance.
2. Restore from latest full backup: `pg_restore --dbname=aipaper <backup_file>`.
3. Apply WAL files for point-in-time recovery: replay to latest consistent point.
4. Update connection strings in Kubernetes Secrets.
5. Restart backend and worker pods.
6. Run data integrity verification queries.
7. Monitor replication setup for new replica.

**Redis Recovery:**

1. Sentinel detects primary failure (< 5s).
2. Sentinel promotes highest-priority replica (< 10s).
3. Clients reconnect to new primary (automatic via Sentinel discovery).
4. If full loss: restore from latest RDB snapshot to new instance.
5. Celery reconnects and re-discovers queue state from Redis. Because tasks are registered with `acks_late=True`, unacknowledged tasks are redelivered; idempotency keys make the redelivery safe.

**Full Platform Recovery (Region Failure):**

1. DNS failover to secondary region (manual or automated with health-check-based routing).
2. Provision Kubernetes cluster in secondary region (if not pre-provisioned).
3. Restore PostgreSQL from cross-region backup (WAL + full backup).
4. Restore Redis from cross-region RDB snapshot.
5. Deploy all services from container registry (images are replicated cross-region).
6. Update object storage endpoints to secondary region bucket.
7. Run smoke tests. Monitor for data consistency.
8. Communicate status to tenants.

### RTO and RPO Targets

| Scenario | RTO Target | RPO Target | Actual (Tested) |
|----------|-----------|-----------|-----------------|
| Single pod failure | < 30 seconds | 0 (no data loss) | < 15 seconds |
| Node failure | < 2 minutes | 0 | < 90 seconds |
| Database primary failure | < 1 minute | 0 (sync replica) | < 30 seconds |
| Redis primary failure | < 1 minute | < 1 second | < 15 seconds |
| Full cluster failure | ≤ 4 hours | ≤ 1 hour | To be measured in drills |
| Region failure | ≤ 4 hours | ≤ 1 hour | To be measured in drills |

---

## 22. Performance Optimization

### Caching Strategy

| Cache Layer | What is Cached | TTL | Invalidation | Expected Hit Rate |
|-------------|---------------|-----|--------------|-------------------|
| CDN | Static assets (JS, CSS, fonts, images) | Immutable (content-hashed filenames) | Deploy invalidation | > 95% |
| Browser | API responses (curriculum, config) | `Cache-Control: max-age=300` | `ETag` / `If-None-Match` | > 60% |
| Redis | Session data | 24 hours | On logout/password change | > 90% |
| Redis | Curriculum tree | 1 hour | On curriculum publish event | > 80% |
| Redis | Rate limit counters | 1 minute | Auto-expiry | N/A |
| PostgreSQL | Query plan cache | Internal | Auto-managed | N/A |

### Compression

| Layer | Method | Content Types | Level |
|-------|--------|---------------|-------|
| Nginx Ingress | gzip | text/html, application/json, text/css, application/javascript | 6 (balanced) |
| Next.js | Brotli (static build) | Pre-compressed JS and CSS bundles | 11 (max for static files) |
| API responses | gzip (via Nginx) | application/json | 6 |

### Connection Pooling

| Connection | Pool Size | Idle Timeout | Max Lifetime |
|-----------|-----------|--------------|-------------|
| Backend → PostgreSQL | 10 per pod | 30 seconds | 30 minutes |
| Worker → PostgreSQL | 5 per pod | 30 seconds | 30 minutes |
| Backend → Redis | 10 per pod | 60 seconds | Unlimited |
| Worker → Redis | 5 per pod | 60 seconds | Unlimited |
| Backend → AI Provider | HTTP keep-alive, 5 concurrent | 30 seconds | 5 minutes |

### CDN Optimization

- **Content hashing:** Next.js generates hashed filenames for all static assets (e.g., `main.a1b2c3d.js`). These are cached as immutable with `Cache-Control: public, max-age=31536000, immutable`.
- **Edge caching:** HTML pages that are ISR (Incremental Static Regeneration) are cached at the CDN edge with `stale-while-revalidate` for fast loads.
- **Image optimization:** Next.js Image component serves optimized, responsive images in WebP/AVIF format via the CDN.

### Database Tuning

| Optimization | Approach |
|-------------|----------|
| Index coverage | Ensure all WHERE clause columns in frequent queries have B-tree indexes |
| Composite indexes | Create composite indexes for multi-column filters (e.g., `org_id, subject_id, status`) |
| Partial indexes | Index only active records (`WHERE deleted_at IS NULL`) to reduce index size |
| Query optimization | Review `pg_stat_statements` weekly; optimize queries with > 100ms average |
| Connection pooling | PgBouncer for > 20 total pods |
| Vacuum tuning | Adjust autovacuum thresholds for high-churn tables (`question_bank`, `audit_logs`) |
| pgvector tuning | Rebuild IVFFlat indexes when row count doubles; consider HNSW at > 1M vectors |

### Worker Optimization

| Optimization | Approach |
|-------------|----------|
| Batch processing | OCR worker processes pages in batches of 10 rather than one-at-a-time |
| AI request batching | Generation worker batches embedding requests (up to 50 texts per API call) |
| Parallel I/O | Export worker uploads rendered files to storage in parallel with PDF generation |
| Job prioritization | Celery priority queues: user-initiated jobs > batch jobs > background reprocessing |
| Memory management | Worker pods set explicit memory limits; OCR worker uses streaming for large PDFs |

---

## 23. Deployment Flow

### Deployment Sequence

```
Developer
    │
    │  1. Push commit to feature branch
    ▼
GitHub Repository
    │
    │  2. PR opened/updated → trigger CI
    ▼
CI Runner (GitHub Actions)
    │
    ├── 3a. Install dependencies
    ├── 3b. Compile TypeScript
    ├── 3c. Run lint + typecheck
    │
    ▼
Test Suite
    │
    ├── 4a. Unit tests (pytest + Vitest)
    ├── 4b. Integration tests (ephemeral DB + Redis)
    ├── 4c. Security scan (npm audit, Trivy)
    │
    ▼
Docker Build
    │
    ├── 5a. Build frontend image
    ├── 5b. Build backend image
    ├── 5c. Build worker images (OCR, Gen, Export)
    ├── 5d. Scan images for CVEs
    │
    ▼
Container Registry
    │
    │  6. Push tagged images (sha + timestamp)
    ▼
Deployment Controller
    │
    ├── 7a. (Staging) kubectl apply → rolling update
    ├── 7b. Run database migrations (K8s Job)
    ├── 7c. Wait for rollout completion
    │
    ▼
Health Checks
    │
    ├── 8a. Readiness probe passes (DB connected, Redis connected)
    ├── 8b. Smoke tests pass (key API endpoints respond)
    ├── 8c. Synthetic transaction succeeds
    │
    ▼
Production
    │
    │  9. (After staging validation) Promote to production
    │     Same sequence: apply → migrate → rollout → health check
    ▼
Monitoring
    │
    │  10. Post-deploy monitoring window (30 min)
    │      Watch error rates, latency, queue depths
    │      Auto-rollback if error rate > 5%
    ▼
Complete ✓
```

### Deployment Sequence Diagram

```
Developer     GitHub      CI Runner     Registry    K8s Staging   K8s Prod
    │            │            │            │            │            │
    │── push ───▶│            │            │            │            │
    │            │── trigger ▶│            │            │            │
    │            │            │            │            │            │
    │            │            │── build ──▶│            │            │
    │            │            │── test     │            │            │
    │            │            │── scan     │            │            │
    │            │            │            │            │            │
    │            │            │── docker ──│            │            │
    │            │            │   build    │            │            │
    │            │            │── push ───▶│            │            │
    │            │            │            │            │            │
    │            │            │── apply ───┼───────────▶│            │
    │            │            │            │            │── migrate  │
    │            │            │            │            │── rollout  │
    │            │            │            │            │            │
    │            │            │◀── health ─┼────────────│            │
    │            │            │   check OK │            │            │
    │            │            │── smoke    │            │            │
    │            │            │   tests    │            │            │
    │            │            │            │            │            │
    │◀── notify ─┼────────────│            │            │            │
    │  "staging   │            │            │            │            │
    │   ready"    │            │            │            │            │
    │            │            │            │            │            │
    │── approve ▶│            │            │            │            │
    │  "deploy    │── trigger ▶│            │            │            │
    │   to prod"  │            │── apply ───┼───────────┼───────────▶│
    │            │            │            │            │            │── migrate
    │            │            │            │            │            │── rollout
    │            │            │◀── health ─┼────────────┼────────────│
    │            │            │   check OK │            │            │
    │            │            │── post-    │            │            │
    │            │            │   deploy   │            │            │
    │            │            │   monitor  │            │            │
    │            │            │            │            │            │
    │◀── notify ─┼────────────│            │            │            │
    │  "prod      │            │            │            │            │
    │   deployed" │            │            │            │            │
```

---

## 24. Operational Procedures

### Deployment Procedure

1. Verify all CI checks pass on the PR.
2. Merge PR to `develop`. Staging auto-deploys.
3. Verify staging: smoke tests, manual spot checks if UI change.
4. Merge `develop` to `main` (via PR with 2 reviews).
5. Production deployment triggers automatically.
6. Monitor post-deploy: error rate, latency, queue depth for 30 minutes.
7. If anomalies detected: initiate rollback (see below).

### Rollback Procedure

1. **Assess:** Check Grafana dashboards for error rate, latency, and failed requests.
2. **Decide:** If error rate > 5% or critical functionality broken, proceed with rollback.
3. **Execute:** `kubectl rollout undo deployment/<name> -n aipaper-app`.
4. **Verify:** Wait for rollout. Check health endpoints. Verify error rate returns to baseline.
5. **Database:** If a migration was applied, assess backward compatibility. If incompatible, run down-migration before rollback.
6. **Communicate:** Post incident summary in Slack. Create post-mortem ticket.

### Scaling Procedure (Manual Override)

1. **Assess:** Identify bottleneck (CPU, memory, queue depth, database connections).
2. **Execute:**
   - Application: `kubectl scale deployment/<name> --replicas=<N> -n <namespace>`.
   - Database: Initiate tier upgrade via cloud provider console.
   - Redis: Initiate tier upgrade via cloud provider console.
3. **Verify:** Monitor metrics post-scale. Ensure new replicas are healthy.
4. **Document:** Log the scaling event and rationale.

### Restart Procedure

1. **Rolling restart (zero downtime):** `kubectl rollout restart deployment/<name> -n <namespace>`.
2. **Force restart (downtime):** `kubectl delete pods -l app=<name> -n <namespace>`.
3. **Post-restart:** Verify all pods healthy. Check logs for startup errors. Verify database connections re-established.

### Database Migration Procedure

1. Review migration SQL in PR. Ensure backward compatibility.
2. Run migration against staging. Verify schema changes.
3. Run integration tests against migrated staging database.
4. On production deploy: migration Job runs before application Deployment.
5. Monitor migration Job logs. If migration fails, the deployment is aborted.
6. For large data migrations: run as a separate background job, not in the migration transaction.

### Incident Response

| Severity | Response Time | Escalation | Communication |
|----------|--------------|------------|---------------|
| **P1 (Critical)** – Platform down, data loss risk | < 15 minutes | On-call engineer → Team lead → CTO | Status page update, tenant notification |
| **P2 (High)** – Major feature broken, degraded for all tenants | < 30 minutes | On-call engineer → Team lead | Slack alert, status page if prolonged |
| **P3 (Medium)** – Single tenant affected, workaround available | < 2 hours | On-call engineer | Ticket created, tenant notified |
| **P4 (Low)** – Minor issue, no user impact | Next business day | Assigned engineer | Ticket created |

**Incident lifecycle:**
1. **Detect:** Alert fires or user reports issue.
2. **Triage:** On-call assesses severity. Declares incident if P1/P2.
3. **Mitigate:** Apply immediate fix (rollback, scale, restart, failover).
4. **Resolve:** Root cause identified and permanent fix applied.
5. **Post-mortem:** Document timeline, root cause, impact, and action items. Blameless.

---

## 25. Cost Optimization

### Right-Sizing

| Component | Initial Size | Review Frequency | Resize Trigger |
|-----------|-------------|-----------------|----------------|
| Backend pods | 0.5 CPU, 512 MB | Monthly | Average CPU < 30% → downsize; > 70% → upsize |
| Frontend pods | 0.25 CPU, 256 MB | Monthly | Average CPU < 30% → downsize |
| Worker pods | Varies (see §14) | Monthly | Average CPU < 20% → downsize |
| Database instance | 4 CPU, 16 GB | Quarterly | CPU < 30% sustained → downsize tier |
| Redis instance | 2 GB | Quarterly | Memory < 40% → downsize |

### Auto-Scaling Cost Control

- **Minimum replicas during off-peak:** Reduce minimum replicas for workers to 0 (using KEDA) during nights and weekends when no generation or export jobs are expected. Scale from zero when jobs appear.
- **Scale-down stabilization:** 5-minute stabilization window prevents rapid scale-up/scale-down cycling.
- **Pod disruption budgets:** Prevent unnecessary pod churn during scale-down.

### Storage Cost Management

| Strategy | Savings Estimate | Implementation |
|----------|-----------------|----------------|
| Lifecycle policies (cold tier after 90d) | 40-60% on aged data | S3 lifecycle rules (see §13) |
| Delete processing artifacts after 30d | Avoid unbounded growth | S3 lifecycle rules |
| Compress backups | 50-70% storage reduction | gzip/zstd compression |
| Right-size database storage | Avoid over-provisioning | Auto-expand enabled, start conservative |

### Reserved Resources

For predictable baseline workloads, reserved instances (1-year or 3-year commitments) provide significant savings:

| Component | On-Demand Monthly | Reserved Monthly | Savings |
|-----------|-------------------|------------------|---------|
| App node pool (3 nodes) | Baseline cost | ~40% less (1-year) | 40% |
| Database instance | Baseline cost | ~35% less (1-year) | 35% |
| Redis instance | Baseline cost | ~30% less (1-year) | 30% |

Worker nodes remain on on-demand or spot instances due to variable load.

### AI Provider Cost Optimization

| Strategy | Impact |
|----------|--------|
| Retrieval-first approach | Avoids LLM calls for questions that exist in the bank |
| Caching AI responses | Identical prompts (same curriculum context) return cached results |
| Model tiering | Gemini **Flash tier** for classification, tagging, and validation support; Gemini **Pro tier** only for generation and hard reasoning |
| Batch embedding requests | Reduce per-request overhead; up to 50 texts per batch API call |
| Token budget per tenant | Prevent runaway costs from a single tenant's abuse |

---

## 26. Maintenance

### Patch Management

| Component | Patch Source | Frequency | Process |
|-----------|-------------|-----------|---------|
| Python runtime (backend) | Official releases | Monthly (or on CVE) | Update base image, rebuild, test, deploy |
| Node.js runtime (frontend) | Official releases | Monthly (or on CVE) | Update base image, rebuild, test, deploy |
| Python + npm dependencies | Dependabot/Renovate PRs | Weekly (minor), immediate (critical CVE) | Review PR, run tests, merge |
| OS packages (Alpine) | Alpine security updates | Monthly | Update base image, rebuild |
| Kubernetes | Cloud provider managed | Quarterly (minor versions) | Test on staging, upgrade production |
| PostgreSQL | Managed service updates | Quarterly (minor), Annual (major) | Schedule maintenance window |
| Redis | Managed service updates | Quarterly | Schedule maintenance window |

### Dependency Updates

- **Automated PRs:** Dependabot or Renovate creates PRs for dependency updates weekly.
- **Review process:** All dependency updates go through CI (build, test, scan) before merge.
- **Major version upgrades:** Manual review. Assessed for breaking changes, performance impact, and security implications.
- **Lock file hygiene:** the Python lock file and `pnpm-lock.yaml` are always committed; CI installs from the lock, never resolving fresh.

### Certificate Renewal

- **TLS certificates:** cert-manager auto-renews Let's Encrypt certificates 30 days before expiry.
- **Monitoring:** Prometheus alert fires if certificate expires within 14 days (catch cert-manager failures).
- **Internal certificates:** If service mesh mTLS is deployed, certificate rotation is handled by the mesh control plane.

### Secret Rotation Schedule

| Secret | Rotation Frequency | Process |
|--------|-------------------|---------|
| `JWT_SECRET` | 90 days | Generate new secret, update K8s Secret, rolling restart |
| `DATABASE_URL` (password) | 90 days | Change password on DB, update K8s Secret, rolling restart |
| `REDIS_PASSWORD` | 90 days | Change password on Redis, update K8s Secret, rolling restart |
| AI API keys | 90 days (or on suspected compromise) | Generate new key in provider console, update K8s Secret, verify |
| S3 credentials | 90 days | Rotate IAM keys, update K8s Secret |

### Database Maintenance

| Task | Frequency | Window | Impact |
|------|-----------|--------|--------|
| VACUUM ANALYZE | Daily (auto) | Off-peak (02:00–04:00 UTC) | None (concurrent) |
| REINDEX (pgvector) | Weekly | Maintenance window (Sunday 03:00 UTC) | Brief lock on affected table |
| pg_stat_statements reset | Monthly | On review | None |
| Bloat cleanup | As needed | Maintenance window | Depends on table size |
| Extension upgrades | On release | Maintenance window | Brief downtime for ALTER EXTENSION |

---

## 27. Testing Deployment

### Smoke Tests

Run automatically after every deployment to verify core functionality:

| Test | Endpoint | Expected | Timeout |
|------|----------|----------|---------|
| API health | `GET /api/health` | 200 OK with `{"status":"ok"}` | 5s |
| API readiness | `GET /api/health/ready` | 200 OK with DB and Redis connected | 10s |
| Frontend load | `GET /` | 200 OK with HTML containing `<div id="__next">` | 10s |
| Auth flow | `POST /api/v1/auth/login` (test user) | 200 OK with valid JWT | 10s |
| List endpoint | `GET /api/v1/boards` (authenticated) | 200 OK with array response | 10s |

### Load Testing

Quarterly load tests using k6 or Artillery to validate scaling behavior:

| Scenario | Target | Duration | Success Criteria |
|----------|--------|----------|-----------------|
| Steady state | 100 concurrent users, 50 req/s | 30 minutes | p99 < 500ms, error rate < 1% |
| Peak load | 500 concurrent users, 200 req/s | 15 minutes | p99 < 2s, error rate < 5%, auto-scale triggers |
| Spike test | 0 → 1000 users in 60 seconds | 5 minutes | Platform remains responsive, no crashes |
| Soak test | 100 concurrent users | 4 hours | No memory leaks, stable latency, no connection exhaustion |
| Worker stress | 500 concurrent OCR jobs | 30 minutes | All jobs complete, no DLQ overflow, worker scales correctly |

### Chaos Testing

Monthly chaos engineering exercises to validate resilience:

| Experiment | Method | Expected Outcome |
|-----------|--------|-----------------|
| Kill random backend pod | `kubectl delete pod` | Traffic redistributed to healthy pods, no user-visible errors |
| Kill database primary | Simulate primary failure | Replica promoted, < 30s write outage, application reconnects |
| Network partition (Redis) | Network policy blocking Redis | Cache misses handled gracefully, queue processing pauses, resumes on recovery |
| CPU stress on worker node | `stress-ng --cpu 4` | HPA scales up workers, existing jobs complete, new jobs process on new pods |
| AI provider failure | Block outbound to AI API | Circuit breaker trips, fallback provider activates, generation continues |

### Backup Verification

| Test | Frequency | Process |
|------|-----------|---------|
| Database restore | Weekly | Restore latest backup to ephemeral instance, run integrity queries |
| Point-in-time recovery | Monthly | Restore to specific timestamp, verify data consistency |
| Full platform recovery | Quarterly | Full DR drill (see §20) |

---

## 28. Future Enhancements

### Multi-Region Deployment

**Current state:** Single-region deployment with cross-region backups.

**Future:** Active-passive multi-region deployment. Primary region serves all traffic. Secondary region has a warm standby cluster that can be promoted within minutes via DNS failover.

**Requirements:** CockroachDB or PostgreSQL with logical replication for multi-region data sync. Cross-region Redis replication. Global CDN with origin failover.

### Blue-Green Deployment

**Current state:** Rolling updates with zero-downtime deployment.

**Future:** Full blue-green deployment where two complete environments (blue and green) run simultaneously. Traffic switches from blue to green atomically via load balancer configuration. Enables instant rollback by switching back.

**Trade-off:** Doubles infrastructure cost during deployment windows. Simplifies rollback to a DNS/LB switch.

### Canary Deployment

**Current state:** All users receive the new version simultaneously during rolling updates.

**Future:** Route a small percentage of traffic (e.g., 5%) to the new version. Monitor error rates and latency. Gradually increase traffic percentage if metrics are healthy. Automatically roll back if metrics degrade.

**Requirements:** Istio or Nginx Ingress canary annotations. Automated metric comparison (Flagger or Argo Rollouts).

### Service Mesh

**Current state:** Direct pod-to-pod communication within the cluster. No mTLS for internal traffic.

**Future:** Deploy Istio or Linkerd for:
- Automatic mTLS between all services.
- Advanced traffic routing (canary, blue-green, fault injection).
- Circuit breakers and retries at the mesh layer.
- Detailed service-to-service telemetry.

**Trade-off:** Added complexity and resource overhead. Justified when the platform scales beyond 10 services or requires regulatory-grade internal encryption.

### Serverless Workers

**Current state:** Workers run as always-on Kubernetes pods with HPA scaling.

**Future:** Run workers as serverless functions (AWS Lambda, Google Cloud Run) that scale to zero when idle and scale up on-demand when jobs appear in the queue.

**Trade-off:** Eliminates idle worker cost. Cold start latency (500ms–2s) may be acceptable for background jobs. Execution time limits (15 min for Lambda) may constrain long-running OCR jobs.

### Edge Deployment

**Current state:** Single-origin frontend with CDN caching for static assets.

**Future:** Deploy Next.js to edge runtimes (Cloudflare Workers, Vercel Edge) for server-side rendering at the edge. Reduces TTFB globally.

**Trade-off:** Edge functions have limited Node.js API compatibility. Backend API calls still route to the origin region.

---

## 29. Deployment Diagrams

### Infrastructure Overview

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                                  INTERNET                                         │
└───────────────────────────────────────┬──────────────────────────────────────────┘
                                        │
                                   ┌────▼────┐
                                   │   CDN   │  Static assets cached at edge
                                   └────┬────┘
                                        │
                                   ┌────▼────┐
                                   │   DNS   │  app.example.com
                                   └────┬────┘
                                        │
                              ┌─────────▼─────────┐
                              │   Load Balancer    │  TLS termination
                              │   (Layer 7)        │  Health checks
                              └─────────┬──────────┘
                                        │
┌───────────────────────────────────────┼──────────────────────────────────────────┐
│                            KUBERNETES CLUSTER                                     │
│                                       │                                           │
│                              ┌────────▼────────┐                                 │
│                              │  Nginx Ingress   │  Path routing                  │
│                              │  Controller      │  Rate limiting                 │
│                              └───┬──────────┬───┘  Security headers             │
│                                  │          │                                     │
│                         /*, ────▶│          │◀──── /api/*                        │
│                                  │          │                                     │
│           ┌──────────────────────▼──┐  ┌────▼─────────────────────────┐          │
│           │      FRONTEND           │  │          BACKEND             │          │
│           │                         │  │                              │          │
│           │  ┌─────┐ ┌─────┐       │  │  ┌─────┐ ┌─────┐ ┌─────┐  │          │
│           │  │ P1  │ │ P2  │       │  │  │ P1  │ │ P2  │ │ P3  │  │          │
│           │  │Next │ │Next │       │  │  │Fast │ │Fast │ │Fast │  │          │
│           │  └─────┘ └─────┘       │  │  └──┬──┘ └──┬──┘ └──┬──┘  │          │
│           │  ┌─────┐               │  │     │       │       │      │          │
│           │  │ P3  │               │  │     └───────┼───────┘      │          │
│           │  │Next │               │  │             │              │          │
│           │  └─────┘               │  │             │              │          │
│           └─────────────────────────┘  └─────────────┼──────────────┘          │
│                                                      │                         │
│                                            ┌─────────┼─────────┐               │
│                                            │         │         │               │
│                                            ▼         ▼         ▼               │
│                                      ┌──────┐  ┌────────┐  ┌────────┐         │
│                                      │Redis │  │  PG    │  │  S3    │         │
│                                      │      │  │Primary │  │ Compat │         │
│                                      │Queue │  │+Replica│  │Storage │         │
│                                      │Cache │  │        │  │        │         │
│                                      └──┬───┘  └────────┘  └────────┘         │
│                                         │                                      │
│                          ┌──────────────┼──────────────┐                       │
│                          │              │              │                       │
│                          ▼              ▼              ▼                       │
│                    ┌──────────┐  ┌──────────┐  ┌──────────┐                   │
│                    │   OCR    │  │   Gen    │  │  Export  │                   │
│                    │  Worker  │  │  Worker  │  │  Worker  │                   │
│                    │  1-8 pod │  │  1-6 pod │  │  1-6 pod │                   │
│                    └──────────┘  └────┬─────┘  └──────────┘                   │
│                                      │                                        │
└──────────────────────────────────────┼────────────────────────────────────────┘
                                       │ HTTPS
                                       ▼
                              ┌──────────────────┐
                              │  AI Providers    │
                              │  (LLM, Embed)   │
                              └──────────────────┘
```

### CI/CD Pipeline Diagram

```
┌─────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│Developer│────▶│  GitHub   │────▶│ Actions  │────▶│  Build   │
│         │push │Repository │ PR  │ Trigger  │     │  Stage   │
└─────────┘     └──────────┘     └──────────┘     └────┬─────┘
                                                       │
                                                       ▼
                                                  ┌──────────┐
                                                  │   Test   │
                                                  │  Stage   │
                                                  │ Unit     │
                                                  │ Integr.  │
                                                  └────┬─────┘
                                                       │
                                                       ▼
                                                  ┌──────────┐
                                                  │ Security │
                                                  │  Scan    │
                                                  │ CVE/SAST │
                                                  └────┬─────┘
                                                       │
                                                       ▼
                ┌──────────┐     ┌──────────┐     ┌──────────┐
                │Production│◀────│ Staging  │◀────│  Docker  │
                │  Deploy  │     │  Deploy  │     │  Build + │
                │          │     │  + Smoke │     │  Push    │
                └──────────┘     └──────────┘     └──────────┘
```

### Networking Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                          VPC / Private Network                     │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │                    Public Subnet                          │    │
│  │  ┌──────────────┐  ┌──────────────┐                      │    │
│  │  │Load Balancer │  │ NAT Gateway  │                      │    │
│  │  │  :443, :80   │  │ (outbound)   │                      │    │
│  │  └──────┬───────┘  └──────┬───────┘                      │    │
│  └─────────┼─────────────────┼──────────────────────────────┘    │
│            │                 │                                     │
│  ┌─────────┼─────────────────┼──────────────────────────────┐    │
│  │         │    Private Subnet (Kubernetes Nodes)            │    │
│  │         ▼                 │                                │    │
│  │  ┌──────────┐            │                                │    │
│  │  │  Ingress │            │                                │    │
│  │  │  :80     │            │                                │    │
│  │  └──┬───┬───┘            │                                │    │
│  │     │   │                │                                │    │
│  │     │   │   ┌────────────┤                                │    │
│  │     │   │   │            │                                │    │
│  │     ▼   ▼   ▼            ▼                                │    │
│  │  App Pods  Worker Pods   Outbound (AI APIs, S3)           │    │
│  │  :3000     (no inbound)  via NAT Gateway                  │    │
│  │  :3001                                                    │    │
│  └───────────────────────────────────────────────────────────┘    │
│                                                                    │
│  ┌───────────────────────────────────────────────────────────┐    │
│  │              Data Subnet (Managed Services)                │    │
│  │  ┌──────────┐  ┌──────────┐                               │    │
│  │  │PostgreSQL│  │  Redis   │                               │    │
│  │  │  :5432   │  │  :6379   │                               │    │
│  │  └──────────┘  └──────────┘                               │    │
│  │  Access: Private subnet only (security groups)            │    │
│  └───────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### Worker Architecture Diagram

```
┌───────────────────────────────────────────────────────────────┐
│                    WORKER PROCESSING FLOW                       │
│                                                                 │
│  ┌────────────┐                                                │
│  │ Redis Queue│                                                │
│  │            │                                                │
│  │ ┌────────┐ │    ┌──────────────────────────────────────┐   │
│  │ │  ocr   │─┼───▶│           OCR Worker                 │   │
│  │ │ waiting│ │    │                                      │   │
│  │ │ active │ │    │  ┌─────┐  ┌──────┐  ┌──────────┐   │   │
│  │ │ failed │ │    │  │Fetch│─▶│OCR   │─▶│ Extract  │   │   │
│  │ └────────┘ │    │  │file │  │pages │  │ assets   │   │   │
│  │            │    │  │from │  │      │  │ (text,   │   │   │
│  │ ┌────────┐ │    │  │S3   │  │      │  │ images)  │   │   │
│  │ │  gen   │─┼───▶│  └─────┘  └──────┘  └────┬─────┘   │   │
│  │ │ waiting│ │    │                           │         │   │
│  │ │ active │ │    │  Write to PG + S3         │         │   │
│  │ │ failed │ │    │  Publish progress event    │         │   │
│  │ └────────┘ │    └───────────────────────────┼─────────┘   │
│  │            │                                │              │
│  │ ┌────────┐ │    ┌───────────────────────────▼─────────┐   │
│  │ │ export │─┼───▶│       Generation Worker             │   │
│  │ │ waiting│ │    │                                      │   │
│  │ │ active │ │    │  ┌────────┐  ┌──────┐  ┌────────┐  │   │
│  │ │ failed │ │    │  │Retrieve│─▶│AI Gen│─▶│Validate│  │   │
│  │ └────────┘ │    │  │candida-│  │(LLM) │  │& save  │  │   │
│  │            │    │  │tes     │  │      │  │        │  │   │
│  │  DLQ:      │    │  └────────┘  └──────┘  └────────┘  │   │
│  │ ┌────────┐ │    │                                      │   │
│  │ │ocr:dlq │ │    │  Read PG, Call AI, Write PG          │   │
│  │ │gen:dlq │ │    └──────────────────────────────────────┘   │
│  │ │exp:dlq │ │                                               │
│  │ └────────┘ │    ┌──────────────────────────────────────┐   │
│  │            │    │         Export Worker                 │   │
│  └────────────┘    │                                      │   │
│                    │  ┌─────┐  ┌──────┐  ┌──────────┐   │   │
│                    │  │Load │─▶│Render│─▶│ Upload   │   │   │
│                    │  │paper│  │PDF/  │  │ to S3    │   │   │
│                    │  │data │  │DOCX  │  │          │   │   │
│                    │  └─────┘  └──────┘  └──────────┘   │   │
│                    │                                      │   │
│                    │  Read PG + Templates, Write S3       │   │
│                    └──────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

### Disaster Recovery Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                        PRIMARY REGION                                 │
│                                                                       │
│  ┌───────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  K8s Cluster  │  │  PostgreSQL  │  │   Object Storage         │  │
│  │  (Active)     │  │  Primary +   │  │   (Primary Buckets)      │  │
│  │               │  │  Replicas    │  │                           │  │
│  └───────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘  │
│          │                 │                        │                 │
│          │                 │ WAL archiving          │ Cross-region    │
│          │                 │ + daily backup         │ replication     │
│          │                 ▼                        ▼                 │
│          │          ┌──────────────────────────────────────────┐     │
│          │          │          Backup Storage (Same Region)     │     │
│          │          │  • WAL archives (continuous)              │     │
│          │          │  • RDB snapshots (hourly)                 │     │
│          │          │  • Daily full backups                     │     │
│          │          └──────────────┬───────────────────────────┘     │
└──────────┼──────────────────────── ┼────────────────────────────────┘
           │                         │
           │ Images in               │ Cross-region
           │ registry                │ replication
           │ (replicated)            │
           ▼                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       SECONDARY REGION                                │
│                                                                       │
│  ┌───────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  K8s Cluster  │  │  PostgreSQL  │  │   Object Storage         │  │
│  │  (Warm        │  │  (Restore    │  │   (Replica Buckets)      │  │
│  │   Standby /   │  │   from       │  │                           │  │
│  │   On-demand)  │  │   backup)    │  │                           │  │
│  └───────────────┘  └──────────────┘  └──────────────────────────┘  │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │           Backup Storage (Cross-Region Copy)                   │  │
│  │  • Replicated WAL archives                                     │  │
│  │  • Replicated daily backups                                    │  │
│  │  • Replicated RDB snapshots                                    │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘

Failover sequence:
  1. DNS switch (app.example.com → Secondary LB)
  2. Restore PostgreSQL from cross-region backup
  3. Deploy services from replicated container registry
  4. Verify and resume operations
  RTO: ≤ 4 hours | RPO: ≤ 1 hour
```

---

## 30. Related Documents

| Document | Relationship |
|----------|-------------|
| [01_Product_Requirements.md](01_Product_Requirements.md) | Business rules, tenant model, rate limits, and plan constraints that the deployment must enforce |
| [02_System_Architecture.md](02_System_Architecture.md) | Logical module boundaries, data flows, and component topology that this document maps to physical infrastructure |
| [03_Database_Design.md](03_Database_Design.md) | Schema, RLS policies, and indexes that inform database deployment, migration, and tuning |
| [05_Textbook_Ingestion.md](05_Textbook_Ingestion.md) | Ingestion pipeline and worker job chain that drive OCR Worker deployment and scaling |
| [06_AI_Architecture.md](06_AI_Architecture.md) | Provider abstraction, prompt versioning, and embedding pipeline that inform AI provider deployment, rate limiting, and fallback configuration |
| [07_Backend_Architecture.md](07_Backend_Architecture.md) | FastAPI module structure, Celery wiring, and the `Depends()` chain that run inside the backend service |
| [08_Frontend_Architecture.md](08_Frontend_Architecture.md) | Next.js rendering strategy, SSR requirements, and asset handling that inform Frontend Deployment and CDN configuration |

---

*Document version: 1.0*
*Last updated: 2026-07-30*
*Authors: Platform Engineering Team*
*Review cycle: Quarterly or on significant infrastructure changes*
