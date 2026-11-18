"""add indexes, constraints and search artifacts (doc 03 §9 step 18)

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-17

doc 03 §9 step 18, in full: *"Add indexes, constraints, RLS policies, partitions, and
seed data."* Five things, and this revision does two of them. The three it does not do
are not oversights — each is named below with the reason, because a step-18 revision that
silently covered two fifths of its brief would be the single easiest thing in this chain
to mistake for complete.

What this revision does
-----------------------
1. **Constraints** — the deferred ``organizations.subscription_id`` foreign key, which
   closes the one expand-migrate-contract sequence in the chain (doc 03 §8). Revision
   0002 shipped the *column* because ``organizations`` is step 2 and ``subscriptions`` is
   step 12; the *constraint* could not exist until its referent did.
2. **Indexes** — the four search artifacts doc 03 §5 specifies on ``question_bank``, plus
   the generated ``tsvector`` column three of them do not need and one of them cannot
   exist without.

What this revision deliberately does *not* do
---------------------------------------------
**RLS policies: left to P1-T5, and this is a binding decision rather than a preference.**
The P1-T4 brief scopes this task to schema only and puts "application-level RLS
enforcement logic" in P1-T5 explicitly. It was raised as an open question before any of
this was written, because doc 03 §9 step 18 says "RLS policies" and doc 03's own
table-by-table specification in §5 never states a policy shape — no ``USING`` clause, no
session variable name, no ``FORCE ROW LEVEL SECURITY`` decision, and no statement of
which of the 27 tables are even in scope. The answer was to leave *all* RLS to P1-T5, and
it is the right one on the merits too: ``CREATE POLICY ... USING (org_id = current_setting(
'app.current_org_id')::uuid)`` is inert — worse, actively misleading — until something
sets that variable on every pooled connection. A policy with no setter looks like tenant
isolation and provides none.

So: **no ``ENABLE ROW LEVEL SECURITY``, no ``CREATE POLICY``, anywhere in revisions 0001
through 0019.** Tenant isolation in P1 rests entirely on ``org_id`` predicates written by
the repository layer, which is P1-T5's subject. This is stated here, in the revision that
doc 03 assigns the work to, so that "the migrations enable RLS" is not assumed by anyone
reading the chain.

**Partitions: not created, because doc 03 does not specify them in a form that can be
built.** Two tables ask for it — ``usage`` and ``audit_logs`` — and revision 0013's
docstring carries the full argument. The short version: a partitioned table's primary key
must contain the partition key, so doc 03's ``id bigint identity, PK`` and the
Blueprint's ``PARTITION BY RANGE (ts)`` cannot both hold, and no interval, retention
window or partition-creation owner is named anywhere. Guessing would produce a table that
is expensive to un-guess: converting a plain table to a partitioned one is a create-copy-
swap migration, not an ``ALTER``. Plain tables, gap reported.

**Seed data: a standalone dev script, not this migration.** ``backend/seed_dev.py``,
idempotent and guarded against non-development environments. Three reasons, in order of
weight: Alembic revisions are forward-only in production, so a seeded row that turns out
to be wrong cannot be corrected by editing the revision that inserted it; doc 03 names no
seed *content* whatsoever, so anything here would be invented data wearing a
schema-migration's authority; and the CBSE/ICSE/state boards a real deployment needs are
tenant-configurable curriculum records, not schema. A migration that inserts them makes
them undeletable-by-design in every environment, including the ones that want their own.

Why the generated column is added *here* and not in step 19
-----------------------------------------------------------
Step 18 says "indexes"; a generated column is neither an index nor a constraint. Step 19
says "backfill … derived search artifacts", which sounds like the column's home. It is
not, and the reason is mechanical: **a STORED generated column is populated by the
``ADD COLUMN`` table rewrite itself.** PostgreSQL evaluates the expression for every
existing row as part of the ALTER; there is no unpopulated interval and therefore nothing
for a later step to backfill. Step 19's search half is vacuous by construction — see
revision 0019's docstring, where it is reported as a doc 03 finding rather than worked
around.

Adding the column here is also forced by ordering: ``ix_question_bank_search_document``
indexes it, and step 18 is where doc 03 puts the indexes.

One operational note, because it does not show up in any catalog dump: ``ADD COLUMN``
with a ``STORED`` generated column takes ``ACCESS EXCLUSIVE`` and **rewrites the whole
table**. On an empty database — which is where this chain runs today — it is
instantaneous. Against a populated ``question_bank`` it is a maintenance-window
operation, and the fact that PostgreSQL offers no ``CONCURRENTLY`` equivalent for it is
worth knowing before this revision meets real data.

Every literal is frozen on purpose — see revision 0003's docstring. ``_SEARCH_DOCUMENT_
EXPRESSION`` is a byte-for-byte copy of the constant in ``app/models/question.py`` rather
than an import of it: a migration that imports application code stops describing the
schema as it was when the migration was written, which is the one thing a migration is
for. The cost is that the two can drift; ``alembic check`` is what catches it, and it runs
at head in the verification script for exactly this reason.

Notes on shape
--------------
* **``op.add_column`` and ``op.create_index``, not ``op.execute``** — a deliberate
  correction to doc 03 §8, which instructs "write partitions, RLS policies, and generated
  ``tsvector`` columns as raw SQL, since autogenerate does not model them". True for
  partitions and policies; **false for the column and all four indexes.** SQLAlchemy 2.x
  models every one of them declaratively (``Computed``, ``postgresql_using``,
  ``postgresql_ops``), they are all declared on ``QuestionBank``, and a column that exists
  in the database but not in ``Base.metadata`` is drift that ``alembic check`` reports and
  the next ``--autogenerate`` run tries to drop. Raw SQL would buy nothing and cost the
  drift check.
* **All four indexes are GIN**, per doc 03 §5 and the Blueprint §8 ("GIN for FTS, trigram,
  and LO arrays"). GIN rather than GiST for all four because GIN is the structure for
  "many keys per row, read far more often than written", which describes a tsvector, a
  UUID array and a trigram set equally.
* **``postgresql_ops={"stem": "gin_trgm_ops"}`` is mandatory, not an optimisation.**
  Without an operator class, GIN over a plain ``text`` column has no usable strategy and
  ``CREATE INDEX`` fails outright. ``pg_trgm`` itself is already installed — revision 0001
  (doc 03 §9 step 1) enables it — so nothing further is needed here.
* **The trigram index is not a second full-text index.** ``search_document`` answers
  "which questions are about photosynthesis"; the trigram index answers what a tsvector
  cannot — FR-3.10's incremental search box (``ILIKE '%photosyn%'``, which no stemmer
  matches) and the near-duplicate check behind AI-4 via ``similarity()``.
* **The two array indexes are what doc 03 §3's design assumed.** Choosing
  ``uuid[]`` columns over bridge tables for ``learning_outcome_ids`` and
  ``competency_ids`` is only defensible if ``@>`` is an index scan, which is what these
  make it. FR-4.19's coverage report filters on exactly that operator.
* **No ``CREATE INDEX CONCURRENTLY``.** It cannot run inside a transaction, and Alembic
  wraps each migration in one; opting out would mean a partially-applied revision on
  failure, which is a worse trade at P1 than a brief lock on an empty table.
* **The foreign key is ``ON DELETE SET NULL``**, matching the declaration on
  ``Organization.subscription_id`` and the mutable-table authorship posture used
  throughout: deleting a subscription record must not delete the tenant pointing at it.
  Note the deliberate asymmetry with ``subscriptions.org_id``, which is CASCADE — the
  child of a deleted organization has no meaning, but the parent of a deleted subscription
  does.

Two open items, reported not resolved
-------------------------------------
1. **``search_document`` is English-only while ``question_bank.language`` is per-row.**
   The generated expression hardcodes ``'english'::regconfig`` and must: a generated
   column's expression has to be IMMUTABLE, so a ``regconfig`` selected from a sibling
   column is not available — the one-argument ``to_tsvector`` is only STABLE because it
   reads ``default_text_search_config`` at runtime. FR-2.6 and the Blueprint both promise
   Hindi and Odia content, and doc 03 §5 asks for one search document per row without
   naming a language. The consequence is not a failure but a quiet quality loss: a Hindi
   stem is tokenised without stemming or stop-word removal, so full-text search over it
   degrades to near-literal matching. The real fixes — one generated column per language,
   or a trigger-maintained column with a dynamic config — are both schema changes doc 03
   does not specify.
2. **Which revision owns the ``organizations.subscription_id`` foreign key is a judgment
   call, and the DDL is identical either way.** Step 12 says only "Create subscriptions";
   step 4 says "Create curriculum_versions **and wire version relations**"; step 18 says
   "constraints". Since §9 demonstrably signals wiring when it wants it, and did not here,
   the constraint was placed in 18 — the step that names constraints outright. The
   alternative reading (0012 adds it the moment its referent exists) produces exactly the
   same schema at head and differs only in which intermediate revision the loop closes at.
   Recorded because three model docstrings were written against the earlier reading and
   corrected alongside this revision, and because the verification script asserts the
   constraint's *absence* below 0018 and its *presence* at head.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Frozen copy of ``app.models.question._SEARCH_DOCUMENT_EXPRESSION``. Not imported —
#: see the module docstring. The two-argument ``to_tsvector`` and the explicit
#: ``::regconfig`` cast are both required for immutability; ``answer::text`` and
#: ``rubric::text`` go through ``jsonb_out``, which is IMMUTABLE, so casting JSONB into
#: the vector is legal here.
_SEARCH_DOCUMENT_EXPRESSION = (
    "to_tsvector('english'::regconfig, "
    "coalesce(stem, '') || ' ' || "
    "coalesce(answer::text, '') || ' ' || "
    "coalesce(rubric::text, ''))"
)


def upgrade() -> None:
    """Upgrade schema."""

    # -- Constraints ---------------------------------------------------------
    # The expand-migrate-contract sequence completing: revision 0002 shipped the column,
    # revision 0012 created the referent, this adds the edge between them.
    op.create_foreign_key(
        op.f("fk_organizations_subscription_id_subscriptions"),
        "organizations",
        "subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # -- Search artifacts ----------------------------------------------------
    # STORED generated column: the ADD COLUMN rewrite populates every existing row, which
    # is why step 19 has nothing left to backfill here.
    op.add_column(
        "question_bank",
        sa.Column(
            "search_document",
            TSVECTOR(),
            sa.Computed(_SEARCH_DOCUMENT_EXPRESSION, persisted=True),
            nullable=True,
        ),
    )
    # doc 03 §5's four GIN indexes. The tsvector index must follow the column above.
    op.create_index(
        op.f("ix_question_bank_search_document"),
        "question_bank",
        ["search_document"],
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_question_bank_learning_outcome_ids"),
        "question_bank",
        ["learning_outcome_ids"],
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_question_bank_competency_ids"),
        "question_bank",
        ["competency_ids"],
        postgresql_using="gin",
    )
    # Hand-named (the convention would yield `ix_question_bank_stem`), so a plain string
    # rather than op.f() — the style revision 0009 set. `gin_trgm_ops` is required for a
    # GIN index over text; without it CREATE INDEX fails.
    op.create_index(
        "ix_question_bank_stem_trgm",
        "question_bank",
        ["stem"],
        postgresql_using="gin",
        postgresql_ops={"stem": "gin_trgm_ops"},
    )


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible, and nothing of value is lost. ``search_document`` is derived, so
    dropping it discards no information that ``ADD COLUMN`` will not recompute on the way
    back up — which is the practical benefit of a generated column over a trigger-
    maintained one.

    The indexes are dropped before the column even though ``DROP COLUMN`` would take
    ``ix_question_bank_search_document`` with it automatically. Explicit is better here:
    the downgrade reads as the exact inverse of the upgrade, and it does not depend on
    PostgreSQL's dependency-tracking behaviour to be correct.

    The foreign key drop leaves ``organizations.subscription_id`` in place — the column
    belongs to revision 0002 and only the constraint belongs to this one, so downgrading
    to 0017 restores the "column without its referent" state that revisions 0002 through
    0017 all ran in.
    """

    op.drop_index("ix_question_bank_stem_trgm", table_name="question_bank")
    op.drop_index(op.f("ix_question_bank_competency_ids"), table_name="question_bank")
    op.drop_index(op.f("ix_question_bank_learning_outcome_ids"), table_name="question_bank")
    op.drop_index(op.f("ix_question_bank_search_document"), table_name="question_bank")
    op.drop_column("question_bank", "search_document")
    op.drop_constraint(
        op.f("fk_organizations_subscription_id_subscriptions"),
        "organizations",
        type_="foreignkey",
    )
