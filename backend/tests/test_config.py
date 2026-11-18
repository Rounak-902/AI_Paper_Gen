import importlib
import os
import subprocess
import sys
from contextlib import contextmanager

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticUndefined

from app.core.config import (
    MIN_SECRET_LENGTH,
    Settings,
    get_settings,
)

VALID_DB_URL = "postgresql+asyncpg://user:securepass123@localhost:5432/papergen"
VALID_JWT_SECRET = "a_very_secure_high_entropy_jwt_secret_with_32_plus_chars_1"
VALID_JWT_REFRESH = "a_very_secure_high_entropy_jwt_refresh_secret_32_plus_chars_2"
VALID_GEMINI_KEY = "AIzaSyFakeValidGeminiApiKeyForTestSuit12345"
# Synthetic IDs on purpose: no concrete model has been selected for the platform,
# so nothing in code or fixtures may imply one.
VALID_AI_MODEL_GENERATE = "test-model-generate-v1"
VALID_AI_MODEL_CLASSIFY = "test-model-classify-v1"
VALID_AI_EMBEDDING_MODEL = "test-model-embedding-v1"

# Every setting that must be supplied by configuration, with no default in code.
REQUIRED_CONFIG_KEYS = (
    "DATABASE_URL",
    "JWT_SECRET",
    "JWT_REFRESH_SECRET",
    "GEMINI_API_KEY",
    "AI_MODEL_GENERATE",
    "AI_MODEL_CLASSIFY",
    "AI_EMBEDDING_MODEL",
)


def valid_config_env(**overrides: object) -> dict:
    """Build a complete, valid environment mapping, with per-key overrides.

    Pass ``KEY=None`` to omit that key entirely, which is how the fail-fast tests
    express "this required variable is absent from the environment".
    """
    env = {
        "DATABASE_URL": VALID_DB_URL,
        "JWT_SECRET": VALID_JWT_SECRET,
        "JWT_REFRESH_SECRET": VALID_JWT_REFRESH,
        "GEMINI_API_KEY": VALID_GEMINI_KEY,
        "AI_MODEL_GENERATE": VALID_AI_MODEL_GENERATE,
        "AI_MODEL_CLASSIFY": VALID_AI_MODEL_CLASSIFY,
        "AI_EMBEDDING_MODEL": VALID_AI_EMBEDDING_MODEL,
        "ENVIRONMENT": "test",
    }
    env.update(overrides)
    return {key: str(value) for key, value in env.items() if value is not None}


def apply_config_env(monkeypatch, tmp_path, env: dict) -> None:
    """Make ``env`` the *only* configuration source visible to Settings.

    ``Settings.model_config`` sets ``env_file=".env"``, resolved relative to the
    process working directory, so the test moves into an empty temporary directory.
    Without that, a developer's local .env could supply the very variable a
    fail-fast test is trying to remove, and the test would pass or fail depending
    on untracked files. Every required key is deleted first so leftovers from
    conftest.py or the host environment cannot leak in.
    """
    monkeypatch.chdir(tmp_path)
    for key in REQUIRED_CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@contextmanager
def fresh_app_main_import():
    """Guarantee the next ``import app.main`` re-executes the module body.

    app/main.py ends with a module-level ``app = create_app()``, which is the
    real fail-fast guarantee. That line only runs on a genuinely fresh import:
    once ``sys.modules["app.main"]`` is populated (for example by test_health.py
    being collected first), a later ``from app.main import ...`` is a cache hit
    and silently skips it. Tests must therefore evict the module themselves
    instead of depending on collection or execution order.

    The previously imported module object is restored on exit, so a fail-fast
    test never leaves a half-built or bad-config ``app.main`` behind for others.
    """
    original = sys.modules.pop("app.main", None)
    get_settings.cache_clear()
    try:
        yield
    finally:
        sys.modules.pop("app.main", None)
        if original is not None:
            sys.modules["app.main"] = original
        get_settings.cache_clear()



def test_valid_settings_load():
    """Verify that Settings loads all typed fields and defaults when given valid inputs."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL=VALID_DB_URL,
        JWT_SECRET=VALID_JWT_SECRET,
        JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
        GEMINI_API_KEY=VALID_GEMINI_KEY,
        AI_MODEL_GENERATE=VALID_AI_MODEL_GENERATE,
        AI_MODEL_CLASSIFY=VALID_AI_MODEL_CLASSIFY,
        AI_EMBEDDING_MODEL=VALID_AI_EMBEDDING_MODEL,
        ENVIRONMENT="test",
    )

    # Required fields
    assert settings.DATABASE_URL == VALID_DB_URL
    assert settings.JWT_SECRET == VALID_JWT_SECRET
    assert settings.JWT_REFRESH_SECRET == VALID_JWT_REFRESH
    assert settings.GEMINI_API_KEY == VALID_GEMINI_KEY
    assert settings.AI_MODEL_GENERATE == VALID_AI_MODEL_GENERATE
    assert settings.AI_MODEL_CLASSIFY == VALID_AI_MODEL_CLASSIFY
    assert settings.ENVIRONMENT == "test"

    # AI Configuration (docs/06_AI_Architecture.md §25.1)
    assert settings.AI_PROVIDER == "gemini"
    assert settings.AI_EMBEDDING_MODEL == VALID_AI_EMBEDDING_MODEL
    assert settings.AI_EMBEDDING_DIMENSIONS == 1536
    assert settings.AI_SIMILARITY_THRESHOLD == 0.92
    assert settings.AI_TOP_K_RETRIEVAL == 20
    assert settings.AI_TEMPERATURE == 0.3
    assert settings.AI_MAX_TOKENS == 2048
    assert settings.AI_RETRY_COUNT == 2
    assert settings.AI_GENERATION_TIMEOUT_MS == 30000
    assert settings.AI_SAFETY_THRESHOLD == "block_medium_and_above"
    assert settings.AI_ESCALATION_THINKING_BUDGET == 4096
    assert settings.AI_TENANT_TOKEN_BUDGET == 100_000
    assert settings.AI_COST_FLASH_INPUT_PER_MILLION_USD == 0
    assert settings.AI_COST_FLASH_OUTPUT_PER_MILLION_USD == 0
    assert settings.AI_COST_PRO_INPUT_PER_MILLION_USD == 0
    assert settings.AI_COST_PRO_OUTPUT_PER_MILLION_USD == 0
    assert settings.AI_COST_ESCALATION_INPUT_PER_MILLION_USD == 0
    assert settings.AI_COST_ESCALATION_OUTPUT_PER_MILLION_USD == 0
    assert settings.AI_SOLVER_TIME_BUDGET_MS == 2000
    assert settings.AI_VERIFY_SANDBOX_TIMEOUT_MS == 2000
    assert settings.CLASSIFICATION_FALLBACK_THRESHOLD == 0.7
    assert settings.TEMPLATE_GENERATION_ENABLED is True
    assert settings.AI_QUALITY_THRESHOLD == 0.70
    assert settings.AI_QUALITY_AUTO_ACCEPT == 0.85
    assert settings.AI_FALLBACK_PROVIDER is None

    # Hybrid Search Weights
    assert settings.AI_HYBRID_WEIGHT_METADATA == 0.4
    assert settings.AI_HYBRID_WEIGHT_FTS == 0.4
    assert settings.AI_HYBRID_WEIGHT_TRIGRAM == 0.2
    assert settings.AI_HYBRID_WEIGHT_VECTOR == 0.0
    assert settings.AI_VECTOR_SEARCH_ENABLED is False

    # HNSW Settings
    assert settings.AI_BATCH_EMBED_SIZE == 2048
    assert settings.AI_HNSW_M == 16
    assert settings.AI_HNSW_EF_CONSTRUCTION == 200
    assert settings.AI_HNSW_EF_SEARCH == 100

    # Ingestion & OCR Settings (docs/05_Textbook_Ingestion.md §27)
    assert settings.OCR_PROVIDER == "mathpix"
    assert settings.TEXTBOOK_MAX_UPLOAD_SIZE_MB == 500
    assert settings.TEXTBOOK_MAX_PAGES == 1000
    assert "pdf" in settings.TEXTBOOK_ALLOWED_TYPES
    assert settings.DEDUP_SIMILARITY_THRESHOLD == 0.95
    assert settings.DEDUP_REVIEW_THRESHOLD == 0.80


def test_ai_max_tokens_reaches_pipeline_request_builder(monkeypatch):
    """The documented deployment limit must reach provider-neutral requests."""
    from types import SimpleNamespace

    from app.modules.ai.pipeline.schemas import Classification, GeneratedContent
    from app.modules.ai.pipeline import service as pipeline_service

    settings = Settings(_env_file=None, **valid_config_env(AI_MAX_TOKENS=2048))
    monkeypatch.setattr(pipeline_service, "get_settings", lambda: settings)
    prompt = SimpleNamespace(layers=SimpleNamespace(
        system="System instructions.", developer="Developer instructions.", user="{{output_schema}}"))

    generation = pipeline_service.render_request(prompt, {}, GeneratedContent)
    classification = pipeline_service.render_request(prompt, {}, Classification)

    assert settings.AI_MAX_TOKENS == 2048
    assert generation.max_tokens == settings.AI_MAX_TOKENS
    assert classification.max_tokens == settings.AI_MAX_TOKENS
    assert generation.max_tokens != 4096


@pytest.mark.parametrize("threshold", [-0.01, 1.01])
def test_dedup_review_threshold_must_be_a_similarity_score(threshold):
    """Reject invalid pg_trgm thresholds during startup, not during an insert."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            **valid_config_env(DEDUP_REVIEW_THRESHOLD=threshold),
        )

    assert "DEDUP_REVIEW_THRESHOLD" in str(exc_info.value)


def test_missing_database_url_fails(monkeypatch):
    """Assert Settings refuses to load when DATABASE_URL is missing or empty."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
        )
    assert "DATABASE_URL" in str(exc_info.value)

    # Empty string should also fail validation
    with pytest.raises(ValidationError) as exc_info_empty:
        Settings(
            _env_file=None,
            DATABASE_URL="",
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
        )
    assert "DATABASE_URL" in str(exc_info_empty.value)


def test_missing_jwt_secret_fails(monkeypatch):
    """Assert Settings refuses to load when JWT_SECRET is missing or empty."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
        )
    assert "JWT_SECRET" in str(exc_info.value)

    # Empty string should also fail validation
    with pytest.raises(ValidationError) as exc_info_empty:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET="",
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
        )
    assert "JWT_SECRET" in str(exc_info_empty.value)


def test_missing_gemini_api_key_fails(monkeypatch):
    """Assert Settings refuses to load when GEMINI_API_KEY is missing or empty."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
        )
    assert "GEMINI_API_KEY" in str(exc_info.value)

    # Empty string should also fail validation
    with pytest.raises(ValidationError) as exc_info_empty:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY="",
            AI_MODEL_GENERATE=VALID_AI_MODEL_GENERATE,
            AI_MODEL_CLASSIFY=VALID_AI_MODEL_CLASSIFY,
        )
    assert "GEMINI_API_KEY" in str(exc_info_empty.value)


def test_missing_ai_model_generate_fails(monkeypatch):
    """Assert Settings refuses to load when AI_MODEL_GENERATE is missing or empty."""
    monkeypatch.delenv("AI_MODEL_GENERATE", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
            AI_MODEL_CLASSIFY=VALID_AI_MODEL_CLASSIFY,
        )
    assert "AI_MODEL_GENERATE" in str(exc_info.value)
    # "Field required" proves the value was absent rather than defaulted
    assert "Field required" in str(exc_info.value)

    # Empty string should also fail validation
    with pytest.raises(ValidationError) as exc_info_empty:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
            AI_MODEL_GENERATE="",
            AI_MODEL_CLASSIFY=VALID_AI_MODEL_CLASSIFY,
        )
    assert "AI_MODEL_GENERATE" in str(exc_info_empty.value)


def test_missing_ai_model_classify_fails(monkeypatch):
    """Assert Settings refuses to load when AI_MODEL_CLASSIFY is missing or empty."""
    monkeypatch.delenv("AI_MODEL_CLASSIFY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
            AI_MODEL_GENERATE=VALID_AI_MODEL_GENERATE,
        )
    assert "AI_MODEL_CLASSIFY" in str(exc_info.value)
    # "Field required" proves the value was absent rather than defaulted
    assert "Field required" in str(exc_info.value)

    # Empty string should also fail validation
    with pytest.raises(ValidationError) as exc_info_empty:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
            AI_MODEL_GENERATE=VALID_AI_MODEL_GENERATE,
            AI_MODEL_CLASSIFY="",
        )
    assert "AI_MODEL_CLASSIFY" in str(exc_info_empty.value)


def test_missing_ai_embedding_model_fails(monkeypatch):
    """Assert the embedding capability has no hidden concrete model fallback."""
    monkeypatch.delenv("AI_EMBEDDING_MODEL", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            **valid_config_env(AI_EMBEDDING_MODEL=None),
        )
    assert "AI_EMBEDDING_MODEL" in str(exc_info.value)
    assert "Field required" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info_empty:
        Settings(
            _env_file=None,
            **valid_config_env(AI_EMBEDDING_MODEL=""),
        )
    assert "AI_EMBEDDING_MODEL" in str(exc_info_empty.value)


def test_placeholder_ai_models_rejected():
    """Assert placeholder model names for all provider capabilities are rejected."""
    with pytest.raises(ValidationError) as exc_info_gen:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
            AI_MODEL_GENERATE="CHANGEME_MODEL",
            AI_MODEL_CLASSIFY=VALID_AI_MODEL_CLASSIFY,
        )
    assert "AI_MODEL_GENERATE" in str(exc_info_gen.value)
    assert "placeholder" in str(exc_info_gen.value).lower()

    with pytest.raises(ValidationError) as exc_info_cls:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
            AI_MODEL_GENERATE=VALID_AI_MODEL_GENERATE,
            AI_MODEL_CLASSIFY="placeholder_model_name",
        )
    assert "AI_MODEL_CLASSIFY" in str(exc_info_cls.value)
    assert "placeholder" in str(exc_info_cls.value).lower()

    with pytest.raises(ValidationError) as exc_info_embed:
        Settings(
            _env_file=None,
            **valid_config_env(AI_EMBEDDING_MODEL="CHANGEME_EMBEDDING_MODEL"),
        )
    assert "AI_EMBEDDING_MODEL" in str(exc_info_embed.value)
    assert "placeholder" in str(exc_info_embed.value).lower()


def test_ai_model_fields_have_no_defaults():
    """Assert all three provider model IDs are required, with no default.

    This is a structural guard: it fails if anyone reintroduces a hardcoded model
    ID (or a default_factory) for any capability, which would let the application boot
    against a model nobody chose.
    """
    for field_name in (
        "AI_MODEL_GENERATE",
        "AI_MODEL_CLASSIFY",
        "AI_EMBEDDING_MODEL",
    ):
        field = Settings.model_fields[field_name]
        assert field.is_required(), f"{field_name} must be a required setting"
        assert field.default is PydanticUndefined, (
            f"{field_name} must not declare a default value (got {field.default!r})"
        )
        assert field.default_factory is None, (
            f"{field_name} must not declare a default_factory"
        )

    # Required in exactly the same way as the other secrets/credentials
    for field_name in ("DATABASE_URL", "JWT_SECRET", "GEMINI_API_KEY"):
        assert Settings.model_fields[field_name].is_required()


def test_placeholder_jwt_secret_rejected():
    """Assert known placeholder patterns for JWT_SECRET are rejected with actionable messages."""
    placeholder_secrets = [
        "CHANGEME_SOME_LONG_SECRET_STRING_OVER_32_CHARS",
        "your-secret-here-must-be-long-enough-to-pass-length-check",
        "placeholder_secret_key_at_least_32_characters_long",
        "replace_me_random_long_secret_key_123456789012",
        "default_secret_string_with_more_than_32_characters",
    ]
    for placeholder in placeholder_secrets:
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                _env_file=None,
                DATABASE_URL=VALID_DB_URL,
                JWT_SECRET=placeholder,
                JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
                GEMINI_API_KEY=VALID_GEMINI_KEY,
            )
        error_msg = str(exc_info.value)
        assert "placeholder" in error_msg.lower()
        assert "openssl rand -base64 64" in error_msg


def test_jwt_secret_under_minimum_length_rejected():
    """Assert JWT secrets shorter than 32 characters are rejected."""
    short_secrets = [
        "short",
        "1234567890123456789012345678901",  # 31 chars
        "almost_thirty_two_chars_long!!",   # 30 chars
    ]
    for short_secret in short_secrets:
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                _env_file=None,
                DATABASE_URL=VALID_DB_URL,
                JWT_SECRET=short_secret,
                JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
                GEMINI_API_KEY=VALID_GEMINI_KEY,
            )
        error_msg = str(exc_info.value)
        assert f"minimum is {MIN_SECRET_LENGTH}" in error_msg
        assert "HS256 requires at least 256 bits" in error_msg


def test_identical_jwt_secrets_rejected():
    """Assert setting identical JWT_SECRET and JWT_REFRESH_SECRET is rejected."""
    shared_secret = "same_secret_shared_between_both_fields_32_chars_long"
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=shared_secret,
            JWT_REFRESH_SECRET=shared_secret,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
        )
    error_msg = str(exc_info.value)
    assert "distinct high-entropy values" in error_msg


def test_invalid_database_url_prefix():
    """Assert DATABASE_URL with non-PostgreSQL scheme is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL="mysql://user:pass@localhost:3306/db",
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
        )
    assert "DATABASE_URL" in str(exc_info.value)
    assert "Must start with postgresql+asyncpg://" in str(exc_info.value)


def test_placeholder_database_url():
    """Assert DATABASE_URL containing placeholder keywords is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://postgres:CHANGEME@localhost:5432/papergen",
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY=VALID_GEMINI_KEY,
        )
    assert "placeholder" in str(exc_info.value).lower()


def test_placeholder_gemini_api_key():
    """Assert GEMINI_API_KEY containing placeholder patterns is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            DATABASE_URL=VALID_DB_URL,
            JWT_SECRET=VALID_JWT_SECRET,
            JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
            GEMINI_API_KEY="CHANGEME_GEMINI_API_KEY",
        )
    assert "placeholder" in str(exc_info.value).lower()


def test_cors_origins_parsing():
    """Assert CORS_ORIGINS parses JSON array strings and comma-separated strings."""
    # JSON array string
    s1 = Settings(
        _env_file=None,
        DATABASE_URL=VALID_DB_URL,
        JWT_SECRET=VALID_JWT_SECRET,
        JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
        GEMINI_API_KEY=VALID_GEMINI_KEY,
        CORS_ORIGINS='["http://localhost:3000", "https://papergen.app"]',
    )
    assert s1.CORS_ORIGINS == ["http://localhost:3000", "https://papergen.app"]

    # Comma-separated string
    s2 = Settings(
        _env_file=None,
        DATABASE_URL=VALID_DB_URL,
        JWT_SECRET=VALID_JWT_SECRET,
        JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
        GEMINI_API_KEY=VALID_GEMINI_KEY,
        CORS_ORIGINS="http://localhost:3000, https://staging.papergen.app",
    )
    assert s2.CORS_ORIGINS == ["http://localhost:3000", "https://staging.papergen.app"]


def test_textbook_allowed_types_parsing():
    """Assert TEXTBOOK_ALLOWED_TYPES parses comma-separated strings correctly."""
    s = Settings(
        _env_file=None,
        DATABASE_URL=VALID_DB_URL,
        JWT_SECRET=VALID_JWT_SECRET,
        JWT_REFRESH_SECRET=VALID_JWT_REFRESH,
        GEMINI_API_KEY=VALID_GEMINI_KEY,
        TEXTBOOK_ALLOWED_TYPES=".pdf, .docx, .png, .jpg",
    )
    assert s.TEXTBOOK_ALLOWED_TYPES == ["pdf", "docx", "png", "jpg"]


def warm_app_main_cache(monkeypatch, tmp_path):
    """Import app.main successfully, so sys.modules holds a good module object.

    Every fail-fast test below starts from this state on purpose. It is exactly the
    state in which a naive `from app.main import create_app` silently skips the
    module-level `app = create_app()`; a fail-fast test that only passes from a cold
    import is testing collection order, not the application.
    """
    apply_config_env(monkeypatch, tmp_path, valid_config_env())
    module = importlib.import_module("app.main")
    assert "app.main" in sys.modules
    return module


def test_fresh_app_main_import_reexecutes_module_body(monkeypatch, tmp_path):
    """Prove the isolation helper really re-runs app.main and then restores state.

    Control test for the fail-fast tests: if this fails, their `pytest.raises`
    blocks are not exercising a fresh initialization at all.
    """
    original = warm_app_main_cache(monkeypatch, tmp_path)

    with fresh_app_main_import():
        reloaded = importlib.import_module("app.main")
        # A distinct module object means the module body (including the
        # module-level `app = create_app()`) executed again under the current env.
        assert reloaded is not original
        assert reloaded.app is not original.app

    # No pollution: the module other tests already imported is put back untouched.
    assert sys.modules["app.main"] is original


def test_app_fail_fast_on_placeholder_jwt_secret_in_process(monkeypatch, tmp_path):
    """Prove importing app.main raises ValidationError when JWT_SECRET is a placeholder."""
    warm_app_main_cache(monkeypatch, tmp_path)
    apply_config_env(
        monkeypatch,
        tmp_path,
        valid_config_env(JWT_SECRET="CHANGEME_PLACEHOLDER_SECRET_KEY_12345678"),
    )

    with fresh_app_main_import():
        with pytest.raises(ValidationError) as exc_info:
            importlib.import_module("app.main")
    assert "JWT_SECRET appears to be a placeholder" in str(exc_info.value)


def test_app_fail_fast_on_missing_database_url_in_process(monkeypatch, tmp_path):
    """Prove importing app.main raises ValidationError when DATABASE_URL is absent."""
    warm_app_main_cache(monkeypatch, tmp_path)
    apply_config_env(monkeypatch, tmp_path, valid_config_env(DATABASE_URL=None))

    with fresh_app_main_import():
        with pytest.raises(ValidationError) as exc_info:
            importlib.import_module("app.main")
    assert "DATABASE_URL" in str(exc_info.value)


def test_app_fail_fast_on_missing_ai_model_generate_in_process(monkeypatch, tmp_path):
    """Prove app startup fails when AI_MODEL_GENERATE alone is absent."""
    warm_app_main_cache(monkeypatch, tmp_path)
    apply_config_env(monkeypatch, tmp_path, valid_config_env(AI_MODEL_GENERATE=None))

    with fresh_app_main_import():
        with pytest.raises(ValidationError) as exc_info:
            importlib.import_module("app.main")
    err_str = str(exc_info.value)
    assert "AI_MODEL_GENERATE" in err_str
    assert "Field required" in err_str
    # The other tier was supplied, so only the absent one may be reported
    assert "AI_MODEL_CLASSIFY" not in err_str


def test_app_fail_fast_on_missing_ai_model_classify_in_process(monkeypatch, tmp_path):
    """Prove app startup fails when AI_MODEL_CLASSIFY alone is absent."""
    warm_app_main_cache(monkeypatch, tmp_path)
    apply_config_env(monkeypatch, tmp_path, valid_config_env(AI_MODEL_CLASSIFY=None))

    with fresh_app_main_import():
        with pytest.raises(ValidationError) as exc_info:
            importlib.import_module("app.main")
    err_str = str(exc_info.value)
    assert "AI_MODEL_CLASSIFY" in err_str
    assert "Field required" in err_str
    assert "AI_MODEL_GENERATE" not in err_str


def test_create_app_fail_fast_on_placeholder_jwt_secret(monkeypatch, tmp_path):
    """Prove create_app() itself validates config on every call, not just at import."""
    # Resolve create_app while the environment is still valid, so the bad config
    # below can only surface through the explicit call inside pytest.raises().
    create_app = warm_app_main_cache(monkeypatch, tmp_path).create_app
    apply_config_env(
        monkeypatch,
        tmp_path,
        valid_config_env(JWT_SECRET="CHANGEME_PLACEHOLDER_SECRET_KEY_12345678"),
    )

    with pytest.raises(ValidationError) as exc_info:
        create_app()
    assert "JWT_SECRET appears to be a placeholder" in str(exc_info.value)


def test_create_app_fail_fast_on_missing_ai_models(monkeypatch, tmp_path):
    """Prove create_app() refuses to build an app when both AI model tiers are absent."""
    create_app = warm_app_main_cache(monkeypatch, tmp_path).create_app
    apply_config_env(
        monkeypatch,
        tmp_path,
        valid_config_env(AI_MODEL_GENERATE=None, AI_MODEL_CLASSIFY=None),
    )

    with pytest.raises(ValidationError) as exc_info:
        create_app()
    err_str = str(exc_info.value)
    assert "AI_MODEL_GENERATE" in err_str
    assert "AI_MODEL_CLASSIFY" in err_str


def run_app_boot_subprocess(tmp_path, env: dict) -> subprocess.CompletedProcess:
    """Import app.main in a brand-new interpreter with exactly ``env`` as config.

    A fresh interpreter is the strongest form of this test: empty sys.modules, no
    lru_cache, environment re-read from scratch. It runs in an empty temporary
    directory so that whatever .env file happens to sit in the repo cannot supply a
    variable the test deliberately removed; `app` stays importable via PYTHONPATH.
    """
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    code = "from app.main import app; print('APP_LOADED_SUCCESSFULLY')"

    proc_env = os.environ.copy()
    proc_env["PYTHONPATH"] = backend_dir
    for key in REQUIRED_CONFIG_KEYS:
        proc_env.pop(key, None)
    proc_env.update(env)

    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=proc_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_app_boots_in_subprocess_with_valid_config(tmp_path):
    """Control test: a fresh interpreter boots the app when configuration is complete.

    Without this, the fail-fast subprocess tests could pass for the wrong reason
    (e.g. an unrelated import error also produces a non-zero exit).
    """
    result = run_app_boot_subprocess(tmp_path, valid_config_env())

    assert result.returncode == 0, result.stderr
    assert "APP_LOADED_SUCCESSFULLY" in result.stdout


@pytest.mark.parametrize(
    "missing_keys",
    [
        ("AI_MODEL_GENERATE",),
        ("AI_MODEL_CLASSIFY",),
        ("AI_EMBEDDING_MODEL",),
        ("AI_MODEL_GENERATE", "AI_MODEL_CLASSIFY", "AI_EMBEDDING_MODEL"),
    ],
    ids=["generate", "classify", "embed", "all-models"],
)
def test_app_fail_fast_on_missing_ai_models_subprocess(tmp_path, missing_keys):
    """Integration test: the app refuses to start when an AI model tier is unset."""
    result = run_app_boot_subprocess(
        tmp_path, valid_config_env(**{key: None for key in missing_keys})
    )

    # Must exit with non-zero status
    assert result.returncode != 0
    output = result.stderr + result.stdout
    assert "ValidationError" in output
    for key in missing_keys:
        assert key in output
    assert "APP_LOADED_SUCCESSFULLY" not in result.stdout


def test_app_fail_fast_on_bad_config_subprocess(tmp_path):
    """Integration test: the app refuses to start with a placeholder secret in the environment."""
    result = run_app_boot_subprocess(
        tmp_path,
        valid_config_env(JWT_SECRET="CHANGEME_PLACEHOLDER_SECRET_KEY_12345678"),
    )

    # Must exit with non-zero status
    assert result.returncode != 0
    output = result.stderr + result.stdout
    assert "ValidationError" in output
    assert "JWT_SECRET" in output
    assert "JWT_SECRET appears to be a placeholder" in output
    assert "APP_LOADED_SUCCESSFULLY" not in result.stdout


