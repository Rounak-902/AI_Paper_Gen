"""Application entrypoint — ``create_app()``.

Doc 07 §3: ``src/main.py  # create_app(): routers, middleware, exception handlers,
lifespan``. All four are wired here and nowhere else.

P1-T6 additions to what P1-T1/T2 built: the lifespan (engine + session factory),
JSON logging, the global exception handlers, the middleware stack, and the probe
routers. ``/health`` is untouched — see :func:`create_app` for why it stays bare.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

from app.common import diagnostics
from app.common.deps import STUB_AUTH_ENVIRONMENTS
from app.common.errors import register_exception_handlers
from app.common.logging_config import configure_logging
from app.common.middleware import (
    AuditLogMiddleware,
    BodySizeLimitMiddleware,
    RequestIdMiddleware,
    TimingMiddleware,
)
from app.core.config import Settings, get_settings
from app.db.engine import create_engine
from app.db.session import init_session_factory
from app.infra.redis import close_redis, init_redis
from app.modules.auth.router import router as auth_router
from app.modules.approvals.router import router as approvals_router
from app.modules.curriculum.router import router as curriculum_router
from app.modules.feasibility.router import router as feasibility_router
from app.modules.notifications.router import router as notifications_router
from app.modules.exports.router import router as exports_router
from app.modules.papers.router import generation_router as paper_generation_router
from app.modules.papers.router import router as papers_router
from app.modules.programs.router import router as programs_router
from app.modules.questions.router import router as questions_router
from app.modules.realtime.router import router as realtime_router
from app.modules.retrieval.diagnostics import router as retrieval_diagnostics_router
from app.modules.tenancy.router import identity_router as tenancy_identity_router
from app.modules.tenancy.router import org_router as tenancy_org_router
from app.modules.tenancy.router import principal_router as tenancy_principal_router
from app.modules.tenancy.router import public_router as tenancy_public_router
from app.modules.templates.router import router as templates_router

logger = logging.getLogger("papergen.api")


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    config_loaded: bool
    environment: str


def _build_lifespan(app_settings: Settings):
    """Engine + session factory + Redis client on startup, disposed on shutdown.

    P1-T5's ``get_tenant_session`` reads a module-level factory that must be
    initialised before the first request; ``init_session_factory`` is that call. It
    happens in the lifespan rather than at import so that importing ``app.main`` never
    touches the database — which is what lets ``tests/test_health.py`` drive the app
    through ``ASGITransport`` (which does not run the lifespan) with no database at all.

    ``create_async_engine`` does not connect, so a bad ``DATABASE_URL`` surfaces on the
    first query rather than here. Adding a startup ``SELECT 1`` would make boot fail
    fast, but it would also make every ``TestClient`` context manager require a live
    database, so it is left to the readiness probe (doc 07 §24.1's ``/health/ready``)
    which is not part of this task.

    P1-T7 adds ``init_redis`` on the same terms and for the same reason. Doc 07 §4.1 makes
    Redis the auth module's session store ("Session state in Redis (not a SQL table)"), so
    it is a correctness dependency of authentication rather than a cache.
    ``Redis.from_url`` does not connect either, so the no-infrastructure test path is
    preserved exactly: a request that needs Redis fails, and one that does not still
    works.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(app_settings)
        init_session_factory(engine)
        init_redis(app_settings)
        logger.info(
            "app_started environment=%s diagnostics_mounted=%s",
            app_settings.ENVIRONMENT,
            app_settings.ENVIRONMENT in STUB_AUTH_ENVIRONMENTS,
        )
        try:
            yield
        finally:
            # Redis first: it is the shallower dependency, and closing it cannot fail in a
            # way that would leave a database connection pool leaked.
            await close_redis()
            await engine.dispose()
            logger.info("app_stopped environment=%s", app_settings.ENVIRONMENT)

    return lifespan


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    # Eagerly load and validate settings so the app fails fast at boot if config is invalid
    app_settings = settings or get_settings()

    configure_logging(app_settings.LOG_LEVEL)

    app = FastAPI(
        title="PaperGen API",
        version="0.1.0",
        description="AI Question Paper Generation Platform API",
        docs_url="/api/docs" if app_settings.ENVIRONMENT != "production" else None,
        redoc_url="/api/redoc" if app_settings.ENVIRONMENT != "production" else None,
        openapi_url="/api/openapi.json" if app_settings.ENVIRONMENT != "production" else None,
        lifespan=_build_lifespan(app_settings),
    )

    # -- Exception handlers (doc 07 §10.1) --------------------------------------
    # Registered before the middleware so that nothing can be added later that
    # short-circuits them.
    register_exception_handlers(app)

    # -- Middleware stack -------------------------------------------------------
    # ``add_middleware`` PREPENDS, so this list is written innermost-first and the
    # resulting stack is the reverse. Desired order, outermost first:
    #
    #   CORS  ->  RequestId  ->  BodySizeLimit  ->  Timing  ->  Audit  ->  router
    #
    # Doc 07 §6 lists the correlation ID *inside* the body cap. It is deliberately
    # outside here: §19.3 requires the id to be "Attached to all log entries during the
    # request" and "Included in error responses (``meta.requestId``)", and a request the
    # body cap rejects would otherwise be answered before an id existed — a 413 carrying
    # ``req_unbound`` and an uncorrelatable log line. See
    # app/common/middleware/__init__.py.
    #
    # CORS is outermost so that its headers are attached even to error responses; a
    # browser cannot read a 500 body it is not allowed to see, which turns every
    # server-side failure into an opaque network error in the UI.
    app.add_middleware(AuditLogMiddleware)
    app.add_middleware(TimingMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RequestIdMiddleware)
    if app_settings.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(app_settings.CORS_ORIGINS),
            # Doc 07 §6: "CORS (strict allow-list, credentials explicit)". Credentials
            # are on because doc 08's client sends the session cookie; a wildcard origin
            # is impossible in that combination, which is the point of the allow-list.
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["X-Request-Id"],
        )

    # -- Routes -----------------------------------------------------------------
    # ``/health`` stays exactly as P1-T1 wrote it: registered on ``app`` with the
    # default route class, so it is NOT enveloped. Two reasons, both concrete. Doc 07
    # §24.1 treats health checks as an operator surface ("no infra detail") consumed by
    # a load balancer that does not parse envelopes, and ``tests/test_health.py``
    # asserts the bare body (``data["status"] == "ok"``). Enveloping it would break a
    # passing test to satisfy a contract the endpoint is not part of.
    @app.get("/health", response_model=HealthResponse, tags=["System"])
    async def health_check(
        current_settings: Settings = Depends(get_settings),
    ) -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="papergen-backend",
            version="0.1.0",
            config_loaded=True,
            environment=current_settings.ENVIRONMENT,
        )

    # -- Auth router (P1-T7) ----------------------------------------------------
    # Mounted in every environment, unlike the diagnostics routers below. It is the one
    # router that is deliberately public — login cannot require the credential it issues —
    # and ``app/modules/auth/router.py`` documents that exception explicitly rather than
    # leaving it as a missing ``dependencies=[...]`` for a reviewer to notice.
    app.include_router(auth_router)

    # -- Tenancy routers (P1-T8) -------------------------------------------------
    # Four routers, split by what each request is allowed to assume:
    # ``public_router`` is unauthenticated (invitation acceptance — the invitee has no
    # account yet), ``identity_router`` and ``principal_router`` require only
    # authentication (``POST /orgs`` creates the org an org context would come from;
    # ``GET /orgs``, ``GET /auth/me`` and ``POST /auth/switch-org`` are all reachable —
    # and must be — while holding an org-less token), and ``org_router`` requires a
    # verified ``activeOrgId``. See ``app/modules/tenancy/router.py`` for the full
    # argument, including why two ``/api/v1/auth`` routes are defined in the tenancy
    # module rather than the auth one (doc 07 §4.2 lists ``GET /auth/me`` under Tenancy,
    # and "Tenancy depends on: Auth module" is a one-way arrow).
    #
    # Registration order is public → identity → principal → org, deliberately. FastAPI
    # matches routes in registration order, and although none of these paths currently
    # collide (``/invitations/accept`` is two segments, ``/invitations/{id}/revoke`` is
    # three), a future ``POST /invitations/{id}`` would shadow ``accept`` if the org
    # router went first. Ordering it this way means the narrower literal path always
    # wins. ``principal_router``'s ``/api/v1/auth`` prefix cannot collide with
    # ``auth_router``'s own routes either — login/refresh/logout are all literal paths
    # and ``me``/``switch-org`` are two more literals under the same prefix.
    app.include_router(tenancy_public_router)
    app.include_router(tenancy_identity_router)
    app.include_router(tenancy_principal_router)
    app.include_router(tenancy_org_router)
    app.include_router(curriculum_router)
    app.include_router(programs_router)
    app.include_router(templates_router)
    app.include_router(questions_router)
    # P4-T2 is synchronous: this route returns before any P4-T4/T5 generation
    # enqueue exists, and it does not import or mutate the P4-T1 worker scaffold.
    app.include_router(feasibility_router)
    # Initial submission reuses the feasibility contract/gate, then creates the
    # paper and prepare_paper outbox row in the same tenant transaction.
    app.include_router(paper_generation_router)
    # P5-T3: explicit lock snapshots enter the same durable P4-T5 residual run.
    app.include_router(papers_router)
    app.include_router(exports_router)
    app.include_router(approvals_router)
    app.include_router(notifications_router)
    # P4-T6: raw StreamingResponse, still protected by the ordinary bearer-token
    # and active-organization guard chain in ``authorize_paper_stream``.
    app.include_router(realtime_router)

    # Probe routers exist to make P1-T6's verification executable against a real app.
    # Mounted only in development/test/local: two of their routes deliberately raise
    # 500s.
    #
    # P1-T8 NOTE: the P1-T6 note here used to say "P1-T8 replaces them with real tenancy
    # CRUD and they should be deleted then". They are **not** deleted, deliberately, for
    # two reasons that both got stronger during this task.
    #
    # First, ``scripts/verify-p1-t6.ps1`` and ``scripts/verify-p1-t7.ps1`` both target
    # ``guarded_router`` directly — it is what proves P1-T7's guard-chain assertions (a
    # real bearer token reaching a handler body, a tampered one not reaching it) and
    # P1-T6's envelope and 500-leak assertions. Deleting them would silently turn two
    # committed, passing verification suites into suites that pass because their target
    # is gone, which is precisely the false-PASS failure mode P1's phase-gate discipline
    # exists to prevent.
    #
    # Second, and new in P1-T8: ``GET /api/v1/_diag/context`` is the **only** route in
    # the application that depends on ``get_scope``, and its ``ContextOut`` already
    # exposes ``branchId`` and ``isOrgWide``. That makes it the live, end-to-end proof
    # that ``get_scope``'s branch resolution returns a real value from a real
    # ``memberships`` row (proof 6 of ``scripts/verify-p1-t8.ps1``). Deleting it would
    # leave that change with no executable proof at all.
    #
    # Recorded in the P1-T8 report; the right time to remove them is when those scripts
    # are retargeted at the tenancy routes above and P2 introduces a real
    # ``get_scope`` consumer.
    if app_settings.ENVIRONMENT in STUB_AUTH_ENVIRONMENTS:
        app.include_router(diagnostics.public_router)
        app.include_router(diagnostics.guarded_router)
        # P3-T1 is an internal service, not a public production API.  This guarded
        # adapter exists only so the phase gate can exercise the real pipeline over
        # HTTP against a running app_user/RLS session.
        app.include_router(retrieval_diagnostics_router)
    else:
        logger.info(
            "diagnostics_routes_not_mounted environment=%s", app_settings.ENVIRONMENT
        )

    return app


# Eagerly instantiate app instance; will fail immediately during import/startup if config is invalid
app = create_app()
