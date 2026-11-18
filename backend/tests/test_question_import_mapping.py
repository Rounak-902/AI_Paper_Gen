"""Focused regression coverage for question-import header auto-mapping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.deps import RequestScope
from app.main import create_app
from app.models.assembly import TaskOutbox
from app.models.audit import AuditLog
from app.models.question import QuestionBank
from app.models.question_import import QuestionImportJob
from app.modules.questions import import_service
from app.modules.questions.import_schemas import ColumnMappingEntry, ImportRowResultOut
from app.modules.questions.import_service import _auto_map_columns, preview_import_file


ID_FIELDS = ("subject_id", "chapter_id", "board_id", "class_id")


async def test_create_import_job_reuses_client_submission_id(
    db_session, tenant
) -> None:
    scope = RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="coaching_admin",
    )
    raw_json = (
        '[{"qtype":"short_answer","stem":"Why?","marks":2,'
        '"difficulty":"medium"}]'
    ).encode()
    mapping = [
        ColumnMappingEntry(source_column=field, target_field=field)
        for field in ("qtype", "stem", "marks", "difficulty")
    ]
    submission_id = uuid.uuid4()

    first = await import_service.create_import_job(
        session=db_session,
        scope=scope,
        org_id=tenant.org_id,
        raw_bytes=raw_json,
        content_type="application/json",
        filename="questions.json",
        column_mapping=mapping,
        job_id=submission_id,
    )
    second = await import_service.create_import_job(
        session=db_session,
        scope=scope,
        org_id=tenant.org_id,
        raw_bytes=raw_json,
        content_type="application/json",
        filename="questions.json",
        column_mapping=mapping,
        job_id=submission_id,
    )

    job_count = (
        await db_session.execute(
            select(func.count()).select_from(QuestionImportJob).where(
                QuestionImportJob.id == submission_id
            )
        )
    ).scalar_one()
    outbox_count = (
        await db_session.execute(
            select(func.count()).select_from(TaskOutbox).where(
                TaskOutbox.idempotency_key
                == f"question-import:{submission_id}:process"
            )
        )
    ).scalar_one()

    assert first.id == submission_id
    assert second.id == submission_id
    assert job_count == 1
    assert outbox_count == 1


def _mapping_by_source(source_columns: list[str]) -> dict[str, str]:
    return {
        entry.source_column: entry.target_field
        for entry in _auto_map_columns(source_columns)
    }


def test_exact_id_headers_map_to_uuid_targets() -> None:
    mapping = _mapping_by_source(
        [
            "qtype",
            "stem",
            "marks",
            "difficulty",
            "bloom",
            "language",
            "source",
            "scope",
            *ID_FIELDS,
            "answer_json",
            "rubric_json",
        ]
    )

    assert {field: mapping[field] for field in ID_FIELDS} == {
        field: field for field in ID_FIELDS
    }


def test_exact_and_id_suffix_matches_are_reserved_before_fuzzy_matches() -> None:
    mapping = _mapping_by_source(
        [
            "subject",
            "legacy_subject_id",
            "chapter",
            "legacy_chapter_id",
            "board",
            "legacy_board_id",
            "class",
            "legacy_class_id",
        ]
    )

    assert mapping == {
        "legacy_subject_id": "subject_id",
        "legacy_chapter_id": "chapter_id",
        "legacy_board_id": "board_id",
        "legacy_class_id": "class_id",
        "subject": "subject_name",
        "chapter": "chapter_name",
        "board": "board_code",
        "class": "class_name",
    }


def test_humanized_id_headers_are_normalized_to_uuid_targets() -> None:
    mapping = _mapping_by_source(["Subject ID", "Chapter-Id", "BOARD ID", "class.id"])

    assert mapping == {
        "Subject ID": "subject_id",
        "Chapter-Id": "chapter_id",
        "BOARD ID": "board_id",
        "class.id": "class_id",
    }


async def test_preview_recommends_exact_ids_for_browser_csv_shape() -> None:
    org_id = uuid.uuid4()
    raw_csv = (
        "qtype,stem,marks,difficulty,bloom,language,source,scope,"
        "subject_id,chapter_id,board_id,class_id,answer_json,rubric_json\n"
        "short_answer,Why?,3,medium,understand,en,institute_bank,org,"
        f"{uuid.uuid4()},{uuid.uuid4()},{uuid.uuid4()},{uuid.uuid4()},"
        '"{""text"":""Because.""}",\n'
    ).encode()

    preview = await preview_import_file(
        scope=RequestScope(
            org_id=org_id,
            user_id=uuid.uuid4(),
            role="coaching_admin",
        ),
        org_id=org_id,
        raw_bytes=raw_csv,
        content_type="text/csv",
        filename="question-import-ui.csv",
    )
    mapping = {
        entry.source_column: entry.target_field
        for entry in preview.recommended_mapping
    }

    assert {field: mapping[field] for field in ID_FIELDS} == {
        field: field for field in ID_FIELDS
    }


async def test_process_import_resumes_after_checkpointed_rows(monkeypatch) -> None:
    org_id = uuid.uuid4()
    seen_rows: list[int] = []
    checkpoints: list[tuple[int, int]] = []

    async def process_row(**kwargs):
        seen_rows.append(kwargs["row_number"])
        return ImportRowResultOut(
            row_number=kwargs["row_number"],
            status="created",
            question_id=uuid.uuid4(),
        )

    async def checkpoint(row: ImportRowResultOut, total_rows: int) -> None:
        checkpoints.append((row.row_number, total_rows))

    monkeypatch.setattr(import_service, "_process_one_row", process_row)
    raw_json = (
        '[{"stem":"one"},{"stem":"two"},{"stem":"three"}]'
    ).encode()
    rows, token = await import_service.process_import(
        scope=RequestScope(
            org_id=org_id,
            user_id=uuid.uuid4(),
            role="coaching_admin",
        ),
        org_id=org_id,
        raw_bytes=raw_json,
        content_type="application/json",
        filename="resume.json",
        column_mapping=[ColumnMappingEntry(source_column="stem", target_field="stem")],
        start_index=1,
        progress_callback=checkpoint,
        cache_report=False,
    )

    assert [row.row_number for row in rows] == [2, 3]
    assert seen_rows == [2, 3]
    assert checkpoints == [(2, 3), (3, 3)]
    assert token is None


async def test_bulk_import_commits_questions_audits_and_one_batch_checkpoint(
    db_connection,
    db_session,
    tenant,
    curriculum,
) -> None:
    factory = async_sessionmaker(
        bind=db_connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    raw_json = (
        "["
        '{"qtype":"short_answer","stem":"Bulk question one",'
        '"marks":2,"difficulty":"medium","scope":"org",'
        f'"subject_id":"{curriculum.subject_id}",'
        '"answer_json":{"text":"Answer one"}},'
        '{"qtype":"short_answer","stem":"Bulk question one",'
        '"marks":2,"difficulty":"medium","scope":"org",'
        f'"subject_id":"{curriculum.subject_id}",'
        '"answer_json":{"text":"Answer one"}},'
        '{"qtype":"short_answer","stem":"Bulk question two",'
        '"marks":3,"difficulty":"easy","scope":"org",'
        f'"subject_id":"{curriculum.subject_id}",'
        '"answer_json":{"text":"Answer two"}}'
        "]"
    ).encode()
    mapping = [
        ColumnMappingEntry(source_column=field, target_field=field)
        for field in (
            "qtype",
            "stem",
            "marks",
            "difficulty",
            "scope",
            "subject_id",
            "answer_json",
        )
    ]
    checkpoints: list[tuple[int, int]] = []

    async def checkpoint(
        _session: AsyncSession,
        rows: list[ImportRowResultOut],
        total_rows: int,
    ) -> None:
        checkpoints.append((len(rows), total_rows))

    results = await import_service.process_import_bulk(
        scope=RequestScope(
            org_id=tenant.org_id,
            user_id=tenant.user_id,
            role="coaching_admin",
        ),
        org_id=tenant.org_id,
        raw_bytes=raw_json,
        content_type="application/json",
        filename="bulk.json",
        column_mapping=mapping,
        session_factory=factory,
        progress_callback=checkpoint,
        batch_size=100,
    )

    question_count = (
        await db_session.execute(
            select(func.count()).select_from(QuestionBank).where(
                QuestionBank.org_id == tenant.org_id,
                QuestionBank.stem.in_(("Bulk question one", "Bulk question two")),
            )
        )
    ).scalar_one()
    audit_count = (
        await db_session.execute(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.org_id == tenant.org_id,
                AuditLog.action == "question.created",
                AuditLog.entity_id.in_(
                    [result.question_id for result in results if result.question_id]
                ),
            )
        )
    ).scalar_one()

    assert [result.status for result in results] == [
        "created",
        "skipped_duplicate",
        "created",
    ]
    assert question_count == 2
    assert audit_count == 2
    assert checkpoints == [(3, 3)]


def test_question_import_is_an_async_pollable_api() -> None:
    paths = create_app().openapi()["paths"]

    submit = paths["/api/v1/orgs/{org_id}/questions/import"]["post"]
    assert "202" in submit["responses"]
    assert (
        submit["responses"]["202"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/SuccessEnvelope_ImportJobOut_"
    )
    assert "/api/v1/orgs/{org_id}/questions/import-jobs/{import_id}" in paths


async def test_create_import_job_persists_upload_and_transactional_outbox(
    db_session, tenant
) -> None:
    scope = RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="coaching_admin",
    )
    raw_json = (
        '[{"qtype":"short_answer","stem":"Why?","marks":2,'
        '"difficulty":"medium"}]'
    ).encode()
    mapping = [
        ColumnMappingEntry(source_column=field, target_field=field)
        for field in ("qtype", "stem", "marks", "difficulty")
    ]

    resource = await import_service.create_import_job(
        session=db_session,
        scope=scope,
        org_id=tenant.org_id,
        raw_bytes=raw_json,
        content_type="application/json",
        filename="questions.json",
        column_mapping=mapping,
    )
    await db_session.commit()

    stored = (
        await db_session.execute(
            select(QuestionImportJob).where(QuestionImportJob.id == resource.id)
        )
    ).scalar_one()
    outbox = (
        await db_session.execute(
            select(TaskOutbox).where(
                TaskOutbox.idempotency_key
                == f"question-import:{resource.id}:process"
            )
        )
    ).scalar_one()

    assert resource.status == "queued"
    assert resource.total_rows == 1
    assert resource.processed_rows == 0
    assert stored.file_bytes == raw_json
    assert outbox.task_name == "papergen.workers.process_question_import"
    assert outbox.payload == {
        "org_id": str(tenant.org_id),
        "import_id": str(resource.id),
    }


async def test_stalled_import_rearms_original_outbox_without_losing_progress(
    db_session, tenant
) -> None:
    scope = RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="coaching_admin",
    )
    raw_json = (
        '[{"qtype":"short_answer","stem":"One","marks":2,"difficulty":"medium"},'
        '{"qtype":"short_answer","stem":"Two","marks":2,"difficulty":"medium"}]'
    ).encode()
    mapping = [
        ColumnMappingEntry(source_column=field, target_field=field)
        for field in ("qtype", "stem", "marks", "difficulty")
    ]
    resource = await import_service.create_import_job(
        session=db_session,
        scope=scope,
        org_id=tenant.org_id,
        raw_bytes=raw_json,
        content_type="application/json",
        filename="stalled.json",
        column_mapping=mapping,
    )
    job = (
        await db_session.execute(
            select(QuestionImportJob).where(QuestionImportJob.id == resource.id)
        )
    ).scalar_one()
    outbox = (
        await db_session.execute(
            select(TaskOutbox).where(
                TaskOutbox.idempotency_key
                == f"question-import:{resource.id}:process"
            )
        )
    ).scalar_one()
    checkpoint = datetime.now(timezone.utc) - timedelta(minutes=10)
    job.status = "processing"
    job.processed_rows = 1
    job.updated_at = checkpoint
    outbox.status = "published"
    outbox.published_at = checkpoint
    await db_session.flush()

    recovered = await import_service.recover_stalled_import_job(
        session=db_session,
        org_id=tenant.org_id,
        import_id=resource.id,
        stale_after_seconds=300,
        now=datetime.now(timezone.utc),
    )

    assert recovered is True
    assert job.status == "queued"
    assert job.processed_rows == 1
    assert job.file_bytes == raw_json
    assert outbox.status == "pending"
    assert outbox.published_at is None
