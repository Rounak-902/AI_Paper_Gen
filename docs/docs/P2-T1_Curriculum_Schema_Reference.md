# P2-T1 Curriculum Schema Reference

Status: effective database schema at Alembic revision `0029`  
Scope: `boards`, `classes`, `subjects`, `chapters`, `learning_outcomes`, and
`curriculum_versions`

This reference was reconciled against the current ORM in
`backend/app/models/curriculum.py`, the shared mixins in `backend/app/db/base.py`,
and every effective curriculum migration through `0029`. The six ORM tables and
the schema produced by `0003`, `0004`, `0028`, and `0029` agree. Later migrations
through `0029` add inbound references and RLS policy decisions but do not alter the
columns below.

## Conventions shared by all six tables

Every default in this document is a PostgreSQL server default. “None” means no
declared default.

| Column | PostgreSQL type | Nullable | Server default | Notes |
| --- | --- | --- | --- | --- |
| `id` | `UUID` | No | `gen_random_uuid()` | Primary key. |
| `created_at` | `TIMESTAMPTZ` | No | `now()` | |
| `updated_at` | `TIMESTAMPTZ` | No | `now()` | The ORM also emits `updated_at = now()` on ORM updates. There is no database update trigger, so direct SQL updates do not advance it automatically. |
| `deleted_at` | `TIMESTAMPTZ` | Yes | None | Soft-delete marker. Normal repositories and API reads exclude non-null rows. |
| `row_version` | `INTEGER` | No | `1` | Optimistic-lock value. Every table has `CHECK (row_version > 0)`. |

Every table therefore has:

- `PRIMARY KEY (id)`, named `pk_<table>`.
- `CHECK (row_version > 0)`, named
  `ck_<table>_row_version_positive`.

All uniqueness rules are partial unique indexes over live rows rather than plain
`UNIQUE` constraints. A soft-deleted row does not block recreation of the same
natural key.

## Relationship chain

The normal hierarchy and version boundary are:

```text
boards.id
  ├─ classes.board_id
  │    └─ subjects.class_id
  │         └─ chapters.subject_id
  │              └─ learning_outcomes.chapter_id
  └─ curriculum_versions.board_id
       ├─ subjects.curriculum_version_id
       └─ chapters.curriculum_version_id

chapters.derived_from_chapter_id ──> chapters.id
```

All foreign keys in this chain use `ON DELETE RESTRICT`. The two version columns
are nullable at the database level for legacy unversioned data, but the current
create API requires a version for subjects and makes version-owned mutations
draft-only.

## `boards`

ORM: `backend/app/models/curriculum.py` (`Board`)  
DDL: `backend/packages/db/migrations/versions/0003_create_curriculum_tables.py`

| Column | PostgreSQL type | Nullable | Server default |
| --- | --- | --- | --- |
| `id` | `UUID` | No | `gen_random_uuid()` |
| `code` | `TEXT` | No | None |
| `name` | `TEXT` | No | None |
| `created_at` | `TIMESTAMPTZ` | No | `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | `now()` |
| `deleted_at` | `TIMESTAMPTZ` | Yes | None |
| `row_version` | `INTEGER` | No | `1` |

Foreign keys: none.

Insert-rejecting constraints:

- `pk_boards`: primary key on `id`.
- `ck_boards_row_version_positive`: `row_version > 0`.
- `uq_boards_code`: unique index on `(code)` where
  `deleted_at IS NULL`.

There is no case folding or whitespace normalization on `code`; equality follows
the database collation. The importer consequently treats board code as an exact,
case-sensitive natural key.

## `classes`

ORM: `backend/app/models/curriculum.py` (`Class`)  
DDL: `backend/packages/db/migrations/versions/0003_create_curriculum_tables.py`

| Column | PostgreSQL type | Nullable | Server default |
| --- | --- | --- | --- |
| `id` | `UUID` | No | `gen_random_uuid()` |
| `board_id` | `UUID` | No | None |
| `name` | `TEXT` | No | None |
| `created_at` | `TIMESTAMPTZ` | No | `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | `now()` |
| `deleted_at` | `TIMESTAMPTZ` | Yes | None |
| `row_version` | `INTEGER` | No | `1` |

Foreign key:

- `fk_classes_board_id_boards`: `board_id -> boards.id`,
  `ON DELETE RESTRICT`.

Insert-rejecting constraints:

- `pk_classes`: primary key on `id`.
- `ck_classes_row_version_positive`: `row_version > 0`.
- `uq_classes_board_id_name`: unique index on `(board_id, name)` where
  `deleted_at IS NULL`.

Other index: `ix_classes_board_id (board_id)`.

## `subjects`

ORM: `backend/app/models/curriculum.py` (`Subject`)  
Initial DDL: `0003_create_curriculum_tables.py`  
Version FK: `0004_create_curriculum_versions.py`  
Effective uniqueness: `0028_fix_curriculum_snapshot_uniqueness.py`

| Column | PostgreSQL type | Nullable | Server default |
| --- | --- | --- | --- |
| `id` | `UUID` | No | `gen_random_uuid()` |
| `class_id` | `UUID` | No | None |
| `curriculum_version_id` | `UUID` | Yes | None |
| `name` | `TEXT` | No | None |
| `created_at` | `TIMESTAMPTZ` | No | `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | `now()` |
| `deleted_at` | `TIMESTAMPTZ` | Yes | None |
| `row_version` | `INTEGER` | No | `1` |

Foreign keys:

- `fk_subjects_class_id_classes`: `class_id -> classes.id`,
  `ON DELETE RESTRICT`.
- `fk_subjects_curriculum_version_id_curriculum_versions`:
  `curriculum_version_id -> curriculum_versions.id`, `ON DELETE RESTRICT`.

Insert-rejecting constraints at `0029`:

- `pk_subjects`: primary key on `id`.
- `ck_subjects_row_version_positive`: `row_version > 0`.
- `uq_subjects_class_id_curriculum_version_id_name`: unique index on
  `(class_id, curriculum_version_id, name)` where
  `deleted_at IS NULL AND curriculum_version_id IS NOT NULL`.
- `uq_subjects_class_id_name_unversioned`: unique index on
  `(class_id, name)` where
  `deleted_at IS NULL AND curriculum_version_id IS NULL`.

Other indexes:

- `ix_subjects_class_id (class_id)`.
- `ix_subjects_curriculum_version_id (curriculum_version_id)`.

Migration `0028` dropped the older live-row unique index on
`(class_id, name)`. The replacement permits the same logical subject name in two
different version snapshots while still preventing duplicates inside one snapshot.
The split unversioned index is necessary because PostgreSQL normally treats nulls as
distinct in a composite unique index.

The database FKs alone do **not** prove that the subject’s version belongs to the
same board as its class. The curriculum service checks that relationship before an
API write.

## `chapters`

ORM: `backend/app/models/curriculum.py` (`Chapter`)  
Initial DDL: `0003_create_curriculum_tables.py`  
Version FK: `0004_create_curriculum_versions.py`  
Direct-self check: `0028_fix_curriculum_snapshot_uniqueness.py`

| Column | PostgreSQL type | Nullable | Server default |
| --- | --- | --- | --- |
| `id` | `UUID` | No | `gen_random_uuid()` |
| `subject_id` | `UUID` | No | None |
| `curriculum_version_id` | `UUID` | Yes | None |
| `seq` | `INTEGER` | No | None |
| `title` | `TEXT` | No | None |
| `derived_from_chapter_id` | `UUID` | Yes | None |
| `created_at` | `TIMESTAMPTZ` | No | `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | `now()` |
| `deleted_at` | `TIMESTAMPTZ` | Yes | None |
| `row_version` | `INTEGER` | No | `1` |

Foreign keys:

- `fk_chapters_subject_id_subjects`: `subject_id -> subjects.id`,
  `ON DELETE RESTRICT`.
- `fk_chapters_curriculum_version_id_curriculum_versions`:
  `curriculum_version_id -> curriculum_versions.id`, `ON DELETE RESTRICT`.
- `fk_chapters_derived_from_chapter_id_chapters`:
  `derived_from_chapter_id -> chapters.id`, `ON DELETE RESTRICT`.

Insert-rejecting constraints:

- `pk_chapters`: primary key on `id`.
- `ck_chapters_row_version_positive`: `row_version > 0`.
- `ck_chapters_derived_from_not_self`:
  `derived_from_chapter_id IS NULL OR derived_from_chapter_id <> id`.
- `uq_chapters_subject_id_seq`: unique index on `(subject_id, seq)` where
  `deleted_at IS NULL`.

Other indexes:

- `ix_chapters_subject_id (subject_id)`.
- `ix_chapters_curriculum_version_id (curriculum_version_id)`.
- `ix_chapters_derived_from_chapter_id (derived_from_chapter_id)`.
- `ix_chapters_curriculum_version_id_subject_id_seq`
  `(curriculum_version_id, subject_id, seq)`.

There is deliberately **no** `CHECK (seq > 0)`. Any integer is legal at both the
database and request-schema layers. The JSON import schema preserves that fact rather
than inventing a positivity rule.

The row-local check rejects only a direct self edge. Longer cycles, same-version
lineage, cross-board lineage, cross-class lineage, cross-subject lineage, and lineage
from a non-active source version require traversal or joins and are enforced by the
curriculum service rather than database CHECK constraints. The API also checks that
the chapter and its subject use the same curriculum version.

## `learning_outcomes`

HTTP resource name: `/api/v1/outcomes`  
ORM: `backend/app/models/curriculum.py` (`LearningOutcome`)  
DDL: `backend/packages/db/migrations/versions/0003_create_curriculum_tables.py`

| Column | PostgreSQL type | Nullable | Server default |
| --- | --- | --- | --- |
| `id` | `UUID` | No | `gen_random_uuid()` |
| `chapter_id` | `UUID` | No | None |
| `code` | `TEXT` | Yes | None |
| `description` | `TEXT` | No | None |
| `created_at` | `TIMESTAMPTZ` | No | `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | `now()` |
| `deleted_at` | `TIMESTAMPTZ` | Yes | None |
| `row_version` | `INTEGER` | No | `1` |

Foreign key:

- `fk_learning_outcomes_chapter_id_chapters`:
  `chapter_id -> chapters.id`, `ON DELETE RESTRICT`.

Insert-rejecting constraints:

- `pk_learning_outcomes`: primary key on `id`.
- `ck_learning_outcomes_row_version_positive`: `row_version > 0`.
- `uq_learning_outcomes_chapter_id_code`: unique index on
  `(chapter_id, code)` where `deleted_at IS NULL`.

Other index: `ix_learning_outcomes_chapter_id (chapter_id)`.

PostgreSQL’s default `NULLS DISTINCT` behavior means multiple live outcomes under
one chapter may all have `code = NULL`; only duplicate non-null codes are rejected.
The bulk-import format intentionally requires a non-null code because the API exposes
no other durable outcome key suitable for deterministic reruns.

## `curriculum_versions`

ORM: `backend/app/models/curriculum.py` (`CurriculumVersion`)  
Initial DDL: `0004_create_curriculum_versions.py`  
One-active rule: `0029_one_active_curriculum_version_per_board.py`

| Column | PostgreSQL type | Nullable | Server default |
| --- | --- | --- | --- |
| `id` | `UUID` | No | `gen_random_uuid()` |
| `board_id` | `UUID` | No | None |
| `academic_year` | `TEXT` | No | None |
| `label` | `TEXT` | Yes | None |
| `status` | `TEXT` | No | `'draft'` |
| `effective_from` | `DATE` | Yes | None |
| `source_ref` | `TEXT` | Yes | None |
| `created_at` | `TIMESTAMPTZ` | No | `now()` |
| `updated_at` | `TIMESTAMPTZ` | No | `now()` |
| `deleted_at` | `TIMESTAMPTZ` | Yes | None |
| `row_version` | `INTEGER` | No | `1` |

Foreign key:

- `fk_curriculum_versions_board_id_boards`: `board_id -> boards.id`,
  `ON DELETE RESTRICT`.

Insert/update-rejecting constraints:

- `pk_curriculum_versions`: primary key on `id`.
- `ck_curriculum_versions_row_version_positive`: `row_version > 0`.
- `ck_curriculum_versions_status`:
  `status IN ('draft', 'active', 'superseded')`.
- `uq_curriculum_versions_board_id_academic_year`: unique index on
  `(board_id, academic_year)` where `deleted_at IS NULL`.
- `uq_curriculum_versions_one_active_per_board`: unique index on `(board_id)`
  where `status = 'active' AND deleted_at IS NULL`.

Other indexes:

- `ix_curriculum_versions_board_id (board_id)`.
- `ix_curriculum_versions_status (status)`.
- `ix_curriculum_versions_effective_from (effective_from)`.

### Exact status vocabulary and lifecycle

`status` is constrained `TEXT`, not a native PostgreSQL enum. The only legal stored
values are:

- `draft`
- `active`
- `superseded`

There are no other constrained-vocabulary columns in these six tables.

The database CHECK permits any change among those three values, but the API service
is stricter: creation always produces `draft`, and updates may move only
`draft -> active -> superseded`. Published (`active` or `superseded`) version metadata
and all version-owned subjects, chapters, and outcomes are immutable. Migration
`0029` independently guarantees that at most one non-deleted `active` version exists
for a board, including under concurrent publication attempts.

## Current API validation that is not database DDL

The database uses unrestricted `TEXT`; the authenticated API imposes the following
request limits:

| Field | API rule |
| --- | --- |
| `boards.code` | 1–64 characters |
| `boards.name` | 1–200 characters |
| `classes.name` | 1–200 characters |
| `subjects.name` | 1–200 characters |
| `chapters.title` | 1–500 characters |
| `learning_outcomes.code` | Null or 1–100 characters |
| `learning_outcomes.description` | 1–4000 characters |
| `curriculum_versions.academic_year` | 1–64 characters; no required year format |
| `curriculum_versions.label` | Null or at most 200 characters |
| `curriculum_versions.source_ref` | Null or at most 1000 characters |
| `curriculum_versions.effective_from` | Null or an ISO date |

The service additionally enforces facts that cannot be expressed by the existing
row-local constraints:

- A subject’s version must belong to its class’s board.
- A chapter’s version must equal its subject’s version.
- Subject, chapter, and outcome mutations require a draft version.
- A lineage source must be live, versioned, in a different **active** version, on the
  same board, and under the same class and exact logical subject name.
- Lineage cannot contain longer cycles.
- Version status moves forward only.

These service rules are why the bulk loader uses the API rather than direct inserts.

## Other references to these tables through `0029`

These inbound relationships do not add columns to the six tables, but they can make
hard deletion fail or preserve historical references:

- `program_subjects.subject_id -> subjects.id`, non-null,
  `ON DELETE RESTRICT` (`0005`).
- Nullable `question_bank.board_id`, `class_id`, `subject_id`, and `chapter_id`
  point to their corresponding curriculum tables with `ON DELETE RESTRICT` (`0006`).
- Nullable `textbooks.subject_id -> subjects.id`, `ON DELETE RESTRICT` (`0008`).
- Nullable `textbook_assets.chapter_id -> chapters.id`,
  `ON DELETE RESTRICT` (`0008`).
- Nullable `generated_papers.curriculum_version_id -> curriculum_versions.id`,
  `ON DELETE SET NULL` (`0010`).
- `question_bank.learning_outcome_ids` is a `UUID[]`, not a foreign key; PostgreSQL
  does not verify those array elements against `learning_outcomes.id`.

The six tables are global catalog data: they have no `org_id` and migration `0025`
deliberately excludes them from row-level security. Reads still require an
authenticated active organization, and mutations require the `super_admin` role.

## Sources checked

- `backend/app/db/base.py`
- `backend/app/models/curriculum.py`
- `backend/app/modules/curriculum/schemas.py`
- `backend/app/modules/curriculum/service.py`
- `backend/packages/db/migrations/versions/0003_create_curriculum_tables.py`
- `backend/packages/db/migrations/versions/0004_create_curriculum_versions.py`
- Curriculum-related inbound FKs in migrations `0005`, `0006`, `0008`, and `0010`
- `backend/packages/db/migrations/versions/0025_enable_rls_and_create_policies.py`
- `backend/packages/db/migrations/versions/0028_fix_curriculum_snapshot_uniqueness.py`
- `backend/packages/db/migrations/versions/0029_one_active_curriculum_version_per_board.py`

## Companion JSON importer

The companion files are:

- `scripts/curriculum-import-schema.json`
- `scripts/curriculum-import-example.json`
- `scripts/load-curriculum.py`

The loader validates the entire document against Draft 2020-12 plus semantic
natural-key and lineage checks before it authenticates. A state-aware dry run then
authenticates and performs only GET requests against curriculum routes:

```powershell
$env:PAPERGEN_API_BASE_URL = 'http://127.0.0.1:8000'
$env:PAPERGEN_SUPER_ADMIN_EMAIL = '<email>'
$env:PAPERGEN_SUPER_ADMIN_PASSWORD = '<password>'
$env:PAPERGEN_SUPER_ADMIN_ORG_ID = '<organization UUID>'

python scripts/load-curriculum.py `
  scripts/curriculum-import-example.json `
  --dry-run
```

Omit `--dry-run` to apply the plan. Credentials have no command-line alternative and
are never printed. The login response must identify the requested organization and
the exact `super_admin` role before planning continues.

### Idempotency keys

The loader lists every page of the relevant API collection and matches exact live
natural keys:

- board: `code`
- class: `(board id, name)`
- curriculum version: `(board id, academic year)`, or an explicitly supplied
  existing version `id`
- subject: `(class id, curriculum version id, name)`
- chapter: `(subject id, seq)`
- learning outcome: `(chapter id, code)`

A key match is skipped only when every supplied non-key field also agrees. A matching
key with a different board name, version metadata, chapter title/lineage, or outcome
description is reported as preflight drift and causes zero curriculum mutations.
Outcome `code` is therefore required by the import schema even though its table
column is nullable.

The import file carries one version context for all nested subjects and chapters.
It may target a real UUID or create/match by academic year. Optional lineage uses
`academicYear + className + subjectName + chapterSeq`; these values are persisted
natural coordinates that can still be resolved on a later standalone run, unlike a
JSON-only chapter alias.

New versions begin as `draft`. The loader creates every descendant first, then
performs any requested forward-only `finalStatus` transition. If activation would
replace another active version, the input must opt in with `supersedeActive: true`;
the loader keeps the prior version active while resolving lineage, then calls the
real API to supersede it immediately before activating the target.

### Atomicity boundary

The API exposes one transaction per HTTP request and no bulk transaction endpoint.
Consequently:

- malformed JSON, JSON Schema failures, duplicate input keys, and direct/current-
  version self-lineage are rejected locally before login and cause zero writes;
- resolvable live-state drift is rejected during read-only API preflight before the
  first curriculum POST;
- a later network failure, concurrency race, or server-side failure can occur after
  earlier API requests committed. The loader reports the exact failed JSON path,
  natural key, payload, and API reason. Rerunning is the recovery path: already
  committed rows are detected by the keys above and skipped.

True all-or-nothing behavior across a whole file would require a new server-side bulk
API, which this scoped follow-up deliberately does not add.
