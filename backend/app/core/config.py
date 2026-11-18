"""Configuration management for the PaperGen application using pydantic-settings.

Validates environment variables at application startup, rejecting placeholder
secrets, invalid URLs, and insecure configurations immediately.
"""

from functools import lru_cache
import json
from typing import Any, List, Optional
from pydantic import (
    Field,
    field_validator,
    model_validator,
    ValidationInfo,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH: int = 32

PLACEHOLDER_SUBSTRINGS: tuple[str, ...] = (
    "changeme",
    "your-secret-here",
    "your_secret_here",
    "your-api-key",
    "your_api_key",
    "placeholder",
    "replace_me",
    "to_be_generated",
    "sample_secret",
    "test_secret_key",
    "default_secret",
)


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file.

    Enforces strict typing, entropy verification for cryptographic keys,
    and immediate boot failure if required parameters are missing or invalid.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # -------------------------------------------------------------------------
    # 1. Environment & Server
    # -------------------------------------------------------------------------
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "info"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

    # -------------------------------------------------------------------------
    # 2. Database (PostgreSQL)
    # -------------------------------------------------------------------------
    DATABASE_URL: str
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False
    DB_SSL: bool = False

    # -------------------------------------------------------------------------
    # 3. Redis & Queue (Celery)
    # -------------------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    QUEUE_REDIS_URL: str = "redis://localhost:6379/1"
    REDIS_TLS: bool = False
    REDIS_MAX_RETRIES: int = 3
    # Redis transports restore an unacknowledged Celery delivery after this
    # interval. Production keeps a conservative default; integration tests can
    # shorten it to prove crash redelivery without waiting an hour.
    CELERY_VISIBILITY_TIMEOUT_SECONDS: int = Field(default=3600, ge=1)
    # The enqueue registry is deliberately longer-lived than a normal job. A
    # caller retry after a timeout must still resolve to the original task.
    TASK_IDEMPOTENCY_TTL_SECONDS: int = Field(default=604800, ge=60)
    # Producer reservations use a short lease until broker publication is
    # confirmed.  A producer killed in reserve->publish therefore cannot poison
    # an idempotency key for the full result TTL (R-20).
    TASK_PUBLISH_RESERVATION_TTL_SECONDS: int = Field(default=15, ge=1)
    # Celery's Redis transport checks expired unacked deliveries while consuming.
    # An explicit pulse keeps that loop moving on an otherwise idle queue (R-19).
    CELERY_QUEUE_KEEPALIVE_SECONDS: float = Field(default=1.0, gt=0)
    CELERY_OUTBOX_POLL_SECONDS: float = Field(default=2.0, gt=0)
    CELERY_SLOT_MAX_RETRIES: int = Field(default=2, ge=0)
    QUESTION_IMPORT_BATCH_SIZE: int = Field(default=500, ge=1, le=1000)
    QUESTION_IMPORT_STALE_SECONDS: int = Field(default=300, ge=60)
    NOTIFICATION_EVENT_POLL_SECONDS: float = Field(default=2.0, gt=0)
    NOTIFICATION_DIGEST_CADENCE_SECONDS: int = Field(default=300, ge=60)
    NOTIFICATION_QUESTION_REVIEW_THRESHOLD: int = Field(default=10, ge=1)
    NOTIFICATION_EMAIL_MAX_RETRIES: int = Field(default=3, ge=0)
    NOTIFICATION_EVENT_MAX_RETRIES: int = Field(default=3, ge=0)

    # Local development points at Mailpit. Production supplies its SMTP relay and
    # credentials through the same adapter; email failure never rolls back in-app rows.
    SMTP_HOST: str = "127.0.0.1"
    SMTP_PORT: int = Field(default=1025, ge=1, le=65535)
    SMTP_FROM_EMAIL: str = "notifications@papergen.local"
    SMTP_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)
    SMTP_STARTTLS: bool = False
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None

    # -------------------------------------------------------------------------
    # 4. Authentication & Security
    # -------------------------------------------------------------------------
    JWT_SECRET: str
    JWT_REFRESH_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRY_MINUTES: int = 15
    JWT_REFRESH_EXPIRY_DAYS: int = 7
    ARGON_MEMORY_COST: int = 65536
    ARGON_TIME_COST: int = 3
    ARGON_PARALLELISM: int = 4

    # -------------------------------------------------------------------------
    # 5. AI Configuration (Gemini & Software-First Pipeline)
    # Docs: docs/06_AI_Architecture.md §25.1
    # -------------------------------------------------------------------------
    GEMINI_API_KEY: str
    AI_PROVIDER: str = "gemini"

    # Model Tier IDs (REQUIRED - must be explicitly chosen per CHANGELOG item #3).
    # Deliberately no defaults: the concrete model ID is a deployment decision and
    # must never silently fall back to a hardcoded model.
    AI_MODEL_GENERATE: str
    AI_MODEL_CLASSIFY: str
    AI_EMBEDDING_MODEL: str

    # Model & Generation Parameters
    AI_TEMPERATURE: float = 0.3
    AI_MAX_TOKENS: int = 2048
    AI_RETRY_COUNT: int = 2
    AI_GENERATION_TIMEOUT_MS: int = 30000
    AI_SAFETY_THRESHOLD: str = "block_medium_and_above"
    AI_ESCALATION_THINKING_BUDGET: int = Field(default=4096, ge=1)

    # The budget gate reserves the worst-case request before every provider
    # attempt.  Callers may replace the in-memory gate with a plan-backed gate,
    # but there is no unbudgeted cascade constructor.
    AI_TENANT_TOKEN_BUDGET: int = Field(default=100_000, ge=1)

    # Provider prices change independently of releases.  Deployment supplies
    # current USD-per-million-token rates; zero means cost is emitted as zero,
    # never guessed from a dated price hardcoded in application code.
    AI_COST_FLASH_INPUT_PER_MILLION_USD: float = Field(default=0.0, ge=0.0)
    AI_COST_FLASH_OUTPUT_PER_MILLION_USD: float = Field(default=0.0, ge=0.0)
    AI_COST_PRO_INPUT_PER_MILLION_USD: float = Field(default=0.0, ge=0.0)
    AI_COST_PRO_OUTPUT_PER_MILLION_USD: float = Field(default=0.0, ge=0.0)
    AI_COST_ESCALATION_INPUT_PER_MILLION_USD: float = Field(default=0.0, ge=0.0)
    AI_COST_ESCALATION_OUTPUT_PER_MILLION_USD: float = Field(default=0.0, ge=0.0)

    # Pipeline Gates & Timeouts
    AI_SOLVER_TIME_BUDGET_MS: int = Field(default=2000, ge=1)
    AI_VERIFY_SANDBOX_TIMEOUT_MS: int = 2000
    CLASSIFICATION_FALLBACK_THRESHOLD: float = 0.7
    TEMPLATE_GENERATION_ENABLED: bool = True
    AI_QUALITY_THRESHOLD: float = 0.70
    AI_QUALITY_AUTO_ACCEPT: float = 0.85
    AI_SIMILARITY_THRESHOLD: float = 0.92
    AI_TOP_K_RETRIEVAL: int = 20
    AI_FALLBACK_PROVIDER: Optional[str] = None

    # Retrieval & Hybrid Search Weights
    AI_HYBRID_WEIGHT_METADATA: float = 0.4
    AI_HYBRID_WEIGHT_FTS: float = 0.4
    AI_HYBRID_WEIGHT_TRIGRAM: float = 0.2
    AI_HYBRID_WEIGHT_VECTOR: float = 0.0
    AI_VECTOR_SEARCH_ENABLED: bool = False

    # Inert Vector & Embedding Settings (Gated for post-MVP adoption per ADR-007)
    AI_EMBEDDING_DIMENSIONS: int = 1536
    AI_BATCH_EMBED_SIZE: int = 2048
    AI_HNSW_M: int = 16
    AI_HNSW_EF_CONSTRUCTION: int = 200
    AI_HNSW_EF_SEARCH: int = 100

    # -------------------------------------------------------------------------
    # 6. Object Storage (S3 / MinIO)
    # -------------------------------------------------------------------------
    STORAGE_ENDPOINT: str = "http://localhost:9000"
    STORAGE_BUCKET: str = "papergen-assets"
    STORAGE_REGION: str = "us-east-1"
    STORAGE_ACCESS_KEY: Optional[str] = None
    STORAGE_SECRET_KEY: Optional[str] = None
    STORAGE_USE_SSL: bool = False
    EXPORT_SIGNED_URL_TTL_SECONDS: int = Field(default=3600, ge=60, le=86400)
    EXPORT_RENDER_TIMEOUT_SECONDS: int = Field(default=120, ge=10, le=600)
    EXPORT_RENDERER_NODE_BINARY: str = "node"

    # -------------------------------------------------------------------------
    # 7. OCR & Ingestion Pipeline (docs/05_Textbook_Ingestion.md §27)
    # -------------------------------------------------------------------------
    OCR_PROVIDER: str = "mathpix"
    MATHPIX_APP_ID: Optional[str] = None
    MATHPIX_APP_KEY: Optional[str] = None
    OCR_FALLBACK_PROVIDER: str = "gemini_vision"
    OCR_PLAINTEXT_PROVIDER: str = "tesseract"
    OCR_TIMEOUT_SECONDS: int = 30
    OCR_JOB_TIMEOUT_SECONDS: int = 600
    OCR_CONFIDENCE_THRESHOLD: float = 0.70
    OCR_PAGE_CONFIDENCE_THRESHOLD: float = 0.60
    OCR_MAX_RETRIES: int = 3
    OCR_DPI: int = 300

    TEXTBOOK_MAX_UPLOAD_SIZE_MB: int = 500
    TEXTBOOK_MAX_PAGES: int = 1000
    TEXTBOOK_ALLOWED_TYPES: List[str] = ["pdf", "docx", "png", "jpg"]
    PARSER_TIMEOUT_SECONDS: int = 120
    CLASSIFIER_TIMEOUT_SECONDS: int = 120
    CLASSIFIER_CONFIDENCE_THRESHOLD: float = 0.65
    EMBEDDING_PROVIDER: str = "gemini"
    # Legacy ingestion-specific override; P7 may opt into it.  Never default a
    # concrete provider model in application code.
    EMBEDDING_MODEL: Optional[str] = None
    EMBEDDING_DIMENSIONS: int = 1536
    EMBEDDING_BATCH_SIZE: int = 2048
    EMBEDDING_MAX_RETRIES: int = 3
    EMBEDDING_TIMEOUT_SECONDS: int = 300
    DEDUP_SIMILARITY_THRESHOLD: float = 0.95
    # PRD quality thresholds: 80-90% is accepted with reviewer warning.
    DEDUP_REVIEW_THRESHOLD: float = Field(default=0.80, ge=0.0, le=1.0)
    IMPORT_BATCH_SIZE: int = 100

    QUEUE_NAME_OCR: str = "ingestion:ocr"
    QUEUE_NAME_PARSER: str = "ingestion:parser"
    QUEUE_NAME_CLASSIFIER: str = "ingestion:classifier"
    QUEUE_NAME_EMBEDDING: str = "ingestion:embedding"
    QUEUE_NAME_IMPORT: str = "ingestion:import"
    SIGNED_URL_UPLOAD_TTL_SECONDS: int = 900
    SIGNED_URL_DOWNLOAD_TTL_SECONDS: int = 3600
    MALWARE_SCAN_ENABLED: bool = True
    CLEANUP_ORPHAN_HOURS: int = 24

    # -------------------------------------------------------------------------
    # 8. Rate Limiting
    # -------------------------------------------------------------------------
    RATE_LIMIT_PER_USER: int = 100
    RATE_LIMIT_PER_ORG: int = 1000
    LOGIN_RATE_LIMIT: int = 50

    # -------------------------------------------------------------------------
    # 9. Observability & Monitoring
    # -------------------------------------------------------------------------
    SENTRY_DSN: Optional[str] = None
    OTEL_ENDPOINT: Optional[str] = None

    # =========================================================================
    # Field Validators
    # =========================================================================

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> List[str]:
        """Parse CORS origins from JSON array string or comma-separated string."""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("[") and v.endswith("]"):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(origin).strip() for origin in v if str(origin).strip()]
        return v

    @field_validator("TEXTBOOK_ALLOWED_TYPES", mode="before")
    @classmethod
    def parse_textbook_types(cls, v: Any) -> List[str]:
        """Parse allowed file types from comma-separated string or list."""
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(item).strip().lower() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            return [ext.strip().lower().lstrip(".") for ext in v.split(",") if ext.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(ext).strip().lower().lstrip(".") for ext in v if str(ext).strip()]
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Validate DATABASE_URL format and ensure it does not contain obvious placeholders."""
        if not v or not v.strip():
            raise ValueError(
                "DATABASE_URL cannot be empty. Please provide a valid PostgreSQL connection string "
                "(e.g. postgresql+asyncpg://user:password@localhost:5432/papergen)."
            )
        v = v.strip()
        v_lower = v.lower()

        # Check for placeholder markers
        for placeholder in PLACEHOLDER_SUBSTRINGS:
            if placeholder in v_lower:
                raise ValueError(
                    f"DATABASE_URL appears to contain placeholder value '{placeholder}'. "
                    "Configure a real PostgreSQL connection string with valid credentials."
                )

        valid_prefixes = ("postgresql://", "postgresql+asyncpg://", "postgres://")
        if not any(v_lower.startswith(prefix) for prefix in valid_prefixes):
            raise ValueError(
                f"DATABASE_URL '{v}' is invalid. Must start with postgresql+asyncpg://, postgresql://, or postgres://"
            )
        return v

    @field_validator("JWT_SECRET", "JWT_REFRESH_SECRET")
    @classmethod
    def validate_jwt_secret_entropy(cls, v: str, info: ValidationInfo) -> str:
        """Validate that JWT secrets have sufficient length and entropy, rejecting placeholders."""
        field_name = info.field_name or "Secret"
        if not v or not v.strip():
            raise ValueError(
                f"{field_name} cannot be empty. "
                "Generate a high-entropy secret with: openssl rand -base64 64 "
                'or python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )

        trimmed = v.strip()
        trimmed_lower = trimmed.lower()

        # Check minimum length (32 characters = 256 bits of entropy for HS256)
        if len(trimmed) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"{field_name} is too short ({len(trimmed)} characters; minimum is {MIN_SECRET_LENGTH}). "
                "HS256 requires at least 256 bits (32 bytes) of cryptographic key material. "
                "Generate a real secret with: openssl rand -base64 64 "
                'or python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )

        # Check for known placeholder patterns
        for placeholder in PLACEHOLDER_SUBSTRINGS:
            if placeholder in trimmed_lower:
                raise ValueError(
                    f"{field_name} appears to be a placeholder containing '{placeholder}'. "
                    "Never use placeholder secrets in any environment. "
                    "Generate a real secret with: openssl rand -base64 64 "
                    'or python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )

        return trimmed

    @field_validator("GEMINI_API_KEY")
    @classmethod
    def validate_gemini_api_key(cls, v: str) -> str:
        """Validate that GEMINI_API_KEY is non-empty and not a placeholder."""
        if not v or not v.strip():
            raise ValueError(
                "GEMINI_API_KEY cannot be empty. "
                "Configure a valid Google Gemini API key from Google AI Studio."
            )
        trimmed = v.strip()
        trimmed_lower = trimmed.lower()
        for placeholder in PLACEHOLDER_SUBSTRINGS:
            if placeholder in trimmed_lower:
                raise ValueError(
                    f"GEMINI_API_KEY appears to be a placeholder containing '{placeholder}'. "
                    "Please provide an authentic Google Gemini API key."
                )
        return trimmed

    @field_validator(
        "AI_MODEL_GENERATE", "AI_MODEL_CLASSIFY", "AI_EMBEDDING_MODEL"
    )
    @classmethod
    def validate_ai_models(cls, v: str, info: ValidationInfo) -> str:
        """Validate that AI model IDs are non-empty and not placeholders.

        No model ID is defaulted in code: the tier IDs are a deployment decision
        and must be supplied through configuration.
        """
        field_name = info.field_name or "AI Model"
        if not v or not v.strip():
            raise ValueError(
                f"{field_name} cannot be empty. "
                "Set it to the model ID selected for this deployment; "
                "no model ID is defaulted in code."
            )
        trimmed = v.strip()
        trimmed_lower = trimmed.lower()
        for placeholder in PLACEHOLDER_SUBSTRINGS:
            if placeholder in trimmed_lower:
                raise ValueError(
                    f"{field_name} appears to be a placeholder containing '{placeholder}'. "
                    "Set it to the model ID selected for this deployment; "
                    "no model ID is defaulted in code."
                )
        return trimmed

    # =========================================================================
    # Model Validators
    # =========================================================================

    @model_validator(mode="after")
    def validate_distinct_jwt_secrets(self) -> "Settings":
        """Ensure JWT_SECRET and JWT_REFRESH_SECRET are distinct values."""
        if (
            hasattr(self, "JWT_SECRET")
            and hasattr(self, "JWT_REFRESH_SECRET")
            and self.JWT_SECRET
            and self.JWT_REFRESH_SECRET
        ):
            if self.JWT_SECRET == self.JWT_REFRESH_SECRET:
                raise ValueError(
                    "JWT_SECRET and JWT_REFRESH_SECRET must be two distinct high-entropy values. "
                    "Reusing the access token secret for refresh tokens is a critical security vulnerability. "
                    "Generate two independent secrets using openssl rand -base64 64."
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton instance of Settings.

    Fails immediately at call time if environment variables are invalid or missing.
    """
    return Settings()
