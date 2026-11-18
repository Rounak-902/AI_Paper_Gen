"""placeholder for gated pgvector adoption (doc 03 §9 step 7) — intentionally empty

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-17

**This revision creates nothing, and that is its purpose.**

doc 03 §9 step 7: "**Gated on ADR-007:** enable the `vector` extension and create
question_embeddings. **Skipped at MVP**; run only when the eval suite records a
paraphrase-recall gap. Nothing earlier in this order depends on it, and the retrieval
interface is unchanged either way."

So step 7 does not run, and revision 0007 is an empty link in the chain that says so
out loud. The alternative — numbering the next migration 0008 with no 0007 on disk —
leaves a hole in ``alembic history`` that reads like a lost file. This way the gate is
a visible artifact: every revision number from here to 0019 keeps lining up 1:1 with a
doc 03 §9 step, and anyone who wonders where embeddings went finds this file and the
ADR reference in it.

Why the table is skipped rather than created empty
--------------------------------------------------
ADR-007 defers pgvector until a **measured** paraphrase-recall gap in the eval suite.
Until then MVP retrieval runs entirely on metadata filter → ``tsvector`` GIN →
``pg_trgm`` → hybrid rank → dedup → coverage → top-K, which needs no vector column
and no ANN index. doc 03 §5's own heading for the table is "**Deferred (ADR-007).**
This table and the `vector` extension are **not created at MVP**."

There is a real contradiction here, resolved deliberately and reported rather than
papered over. doc 03 §5 also lists ``embedding vector, required`` among the table's
columns, and doc 03 §8 says "``QuestionEmbedding`` is defined but its migration is
gated on pgvector adoption". Three statements that cannot all hold:

1. §5 heading and §9 step 7 — table not created at MVP;
2. §5 column list — the table has a required ``vector`` column;
3. §8 — the *model* exists even though the table does not.

(1) wins, on the grounds that it is the statement ADR-007 actually decides and the
other two are descriptions of the post-adoption shape. (2) is moot once (1) holds —
there is no table to give a column to, and creating the column would require
``CREATE EXTENSION vector``, which step 1 explicitly excludes. (3) is *not* moot and
is the one genuinely given up: ``QuestionEmbedding`` is omitted from
``app/models/__init__.py`` too, because a model in ``Base.metadata`` with no table in
the database makes ``alembic check`` — the objective proof that these hand-written
revisions match the models — report permanent drift, and suppressing it via
``include_object`` in ``env.py`` would blunt that check for every future table as well.
Losing a dormant class is cheaper than losing the drift detector.

What adoption will look like
----------------------------
Not an edit to this file. When the trigger fires, the adoption migration is written as
a **new head** (``op.execute("CREATE EXTENSION IF NOT EXISTS vector")``, then
``question_embeddings`` with ``embedding vector(1536)`` per ADR-008 — 1536 rather than
Gemini's native 3072 because HNSW and IVFFlat cap at 2000 dimensions — plus the
``QuestionEmbedding`` model and a partial unique index for "one current embedding per
question per model"). Already-deployed databases have 0007 in ``alembic_version``
history; rewriting an applied revision would make this file's recorded effect and the
database disagree, which is the precise failure the frozen-literal rule in revision
0003 exists to prevent. doc 03 §5 also promises adoption is "an additive migration
rather than a redesign", and a new head is what makes that true.

``textbooks.status`` in revision 0008 includes an ``embedded`` value that is
unreachable while this gate holds. Reported as an open item; the vocabulary is doc
03 §7.3's, and pruning it would be the silent resolution.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op. doc 03 §9 step 7 is gated on ADR-007 and does not run at MVP.

    Deliberately empty rather than absent — see the module docstring. ``alembic
    upgrade`` will report this revision as applied, which is correct: the step was
    reached and its gate was closed.
    """


def downgrade() -> None:
    """No-op, symmetrically.

    Nothing was created, so nothing is dropped. ``downgrade -1`` from revision 0008
    lands here on a schema identical to revision 0006's, which is exactly right.
    """
