"""QuestionBank-specific repository with lineage rooting (P1-T5, Tier-A item 4).

Tier-A item 4 from the P1-T4 follow-up:

    "Every ``question_bank`` insert must set ``root_question_id = id`` on the
     row's own id."

This belongs in the repository layer, not a trigger — a trigger cannot reference
``id`` before it is assigned by ``gen_random_uuid()``, and a ``BEFORE INSERT``
trigger that reads ``NEW.id`` after the default has been evaluated would work but
was explicitly excluded from Tier-A ("pure application logic you're building now").

Why a subclass rather than logic in the base class
---------------------------------------------------
* ``root_question_id = id`` is specific to the question lineage model (ADR-003, BR-8).
  No other table has this pattern.
* The base class should not know about lineage columns, ``previous_version_id``, or
  the distinction between first versions and forks.
* A subclass keeps the base class generic and testable with any model.

Lineage rules
-------------
* **First version** (``previous_version_id`` is None): set ``root_question_id = id``
  (self-rooting). This is the row that starts a lineage.
* **Forked version** (``previous_version_id`` is set): inherit ``root_question_id``
  from the parent. The caller must provide it — the repository validates that it is
  present and not None.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import BaseRepository
from app.models.question import QuestionBank


class QuestionBankRepository(BaseRepository[QuestionBank]):
    """Repository for ``question_bank`` with lineage-rooting on insert."""

    model_class = QuestionBank

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        super().__init__(session, org_id)

    async def create(self, **kwargs: Any) -> QuestionBank:
        """Insert a question with automatic lineage rooting.

        If ``previous_version_id`` is None (first version), this sets
        ``root_question_id = id`` after the flush assigns the id.

        If ``previous_version_id`` is set (forked version), the caller must
        provide ``root_question_id`` — it is inherited from the parent version.

        :raises ValueError: if this is a fork but ``root_question_id`` was not provided.
        """
        previous_version_id = kwargs.get("previous_version_id")
        caller_root = kwargs.get("root_question_id")

        if previous_version_id is not None:
            # This is a fork — root_question_id must be inherited from parent
            if caller_root is None:
                raise ValueError(
                    "A forked question (previous_version_id is set) must provide "
                    "root_question_id inherited from the parent version. "
                    "Do not leave it to the repository to guess."
                )

        # Create the row via the base class (handles org_id injection and flush)
        question = await super().create(**kwargs)

        if previous_version_id is None:
            # First version — self-root the lineage
            question.root_question_id = question.id
            # The flush below writes the UPDATE that sets root_question_id = id
            await self._session.flush()

        return question
