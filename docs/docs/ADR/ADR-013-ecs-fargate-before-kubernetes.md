# ADR-013: ECS Fargate at MVP; Kubernetes on a measured trigger

- **Status:** Accepted — resolves conflict #5
- **Date:** 2026-08-13
- **Affects:** `09_Deployment.md`, `02_System_Architecture.md`, Blueprint §15

## Context

`09_Deployment.md` specified Kubernetes across all environments: five namespaces, three node pools,
HPAs with a Prometheus adapter, cert-manager, network policies, and an Nginx ingress controller. The
Blueprint specified ECS Fargate at MVP with EKS only when scale demands it.

What is actually being deployed at MVP is **one API service, one frontend service, three worker
services, one managed Postgres, and one managed Redis**. A Kubernetes control plane for that is not
free: cluster upgrades on the provider's cadence, node-pool sizing and patching, an ingress controller
to operate, cert-manager to keep working, RBAC and network policies to maintain, and a class of failure
modes (scheduling, DNS, CNI) that simply do not exist without it. That burden lands on a team of 5–6
with half a DevOps engineer.

The counter-argument — that starting on Kubernetes avoids a later migration — assumes the migration is
expensive. It is not, if the manifests are written in advance and the topology maps cleanly.

## Decision

**Docker Compose for development, ECS Fargate for MVP and Beta production, Kubernetes on a named
trigger.**

| Stage | Target |
|---|---|
| Development | Docker Compose — one command, real Postgres/Redis/MinIO, per-service log streams |
| CI | Docker Compose / Testcontainers |
| **MVP → Beta production** | **AWS ECS Fargate** — one service each for `api`, `web`, `worker-ocr`, `worker-generation`, `worker-export` |
| GA → scale | **Kubernetes**, per `09_Deployment.md` §7 |

**Fargate covers every requirement the K8s design was there to satisfy:** rolling deploys with a
deployment controller, health-gated traffic via ALB target groups, autoscaling (CPU and request count
for services, a CloudWatch queue-depth metric for workers), one CloudWatch log group per service,
task-definition secrets from Secrets Manager, one least-privilege task role per service, and rollback
by redeploying the previous immutable task-definition revision.

**Adoption triggers for Kubernetes — any one is sufficient:**

- More than roughly six independently deployable services, where per-service scheduling policy starts
  to matter more than the control-plane cost.
- A need for scheduling primitives Fargate does not express: node affinity, **GPU node pools** (the most
  likely driver, if OCR moves to a self-hosted vision model), or fine-grained spot/on-demand mixing.
- Multi-region active-active, where one orchestration API across regions repays its overhead.

**The Kubernetes manifests are written and validated in CI from day one.** `09_Deployment.md` §7 is
retained in full and reframed as the scale target rather than deleted. Writing them early keeps the
migration a configuration exercise; *applying* them early buys the operational burden with none of the
benefit.

## Consequences

**Positive.** No cluster to operate during the phase when engineering time is most scarce and most
valuable. Lower fixed infrastructure cost — the Blueprint's ~$250–500/month MVP figure assumes no
control plane. Fewer failure modes: no scheduler, no CNI, no ingress controller, no cert-manager. A
smaller on-call surface for a team without a dedicated platform engineer.

**Negative / accepted.** Some AWS coupling — Fargate task definitions, Application Auto Scaling
policies, and CloudWatch metrics are not portable the way manifests are. This is bounded because the
application is plain containers and the K8s manifests already exist as the portable description.
Fargate's autoscaling is coarser than a custom-metrics HPA, which is adequate for queue-depth-driven
workers and CPU-driven services. Per-task cost is higher than well-utilised EC2 nodes — irrelevant at
five services, and one of the things the scale trigger accounts for.

**Migration path.** Every concept maps: Deployment → service, HPA → Application Auto Scaling policy,
Secret → task-definition secret, ServiceAccount → task role, NetworkPolicy → security group. The
mapping is intentionally clean, which is what makes the later move mechanical rather than a rewrite.

## Alternatives considered

- **Kubernetes everywhere from day one** (doc 09's original). The right end state, and it avoids a
  future migration. Rejected for MVP: it front-loads weeks of DevOps work and a permanent operational
  tax to solve scaling problems that five services and one database do not have. The manifests are kept
  so the option stays open at near-zero carrying cost.
- **A managed Kubernetes offering with reduced ops (EKS Auto Mode, GKE Autopilot).** A genuine middle
  ground that removes node management. Still leaves cluster upgrades, ingress, cert-manager, and K8s
  RBAC. Worth revisiting *at* the trigger rather than before it.
- **Plain EC2 with systemd or Docker Compose in production.** Cheapest and simplest, and viable for a
  single-service product. Rejected: no rolling deploys, no health-gated traffic shifting, and manual
  capacity management — it trades operational burden for operational risk on the deploy path.
- **Fully serverless (Lambda/Cloud Run).** Attractive for scale-to-zero worker economics. Rejected for
  MVP: cold starts hurt the interactive path, execution-time limits collide with long OCR jobs, and
  connection pooling against Postgres becomes a problem to engineer around. Listed as a future
  enhancement for workers specifically.
