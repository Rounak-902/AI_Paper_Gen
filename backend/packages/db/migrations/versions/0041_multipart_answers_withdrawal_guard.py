"""Split multipart answers and make approval withdrawal one-shot.

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-30
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041"
down_revision: Union[str, Sequence[str], None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MULTIPART = {"case_study", "source_based"}
_STALE_ARTIFACT_ERROR = (
    "R-27: invalidated because the stored multipart answer key was incomplete; "
    "regenerate this artifact"
)


def _paper_versions():
    return sa.table(
        "paper_versions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("content", postgresql.JSONB()),
        sa.column("answer_key", postgresql.JSONB()),
    )


def _export_artifacts():
    return sa.table(
        "export_artifacts",
        sa.column("paper_version_id", postgresql.UUID(as_uuid=True)),
        sa.column("artifact_type", sa.Text()),
        sa.column("status", sa.Text()),
        sa.column("error", sa.Text()),
    )


def _subquestion_id(slot_id: str, item: Mapping[str, Any], position: int) -> str:
    existing = item.get("subQuestionId") or item.get("id")
    return str(existing) if existing else f"{slot_id}:sub-{position}"


def _split_multipart_answers(
    content_value: Any, answer_key_value: Any
) -> tuple[Any, Any, bool]:
    content = copy.deepcopy(content_value)
    answer_key = copy.deepcopy(answer_key_value)
    if not isinstance(content, dict):
        return content_value, answer_key_value, False
    sections = content.get("sections")
    if not isinstance(sections, list):
        return content_value, answer_key_value, False
    if not isinstance(answer_key, dict):
        answer_key = {"questions": []}
    answers = answer_key.setdefault("questions", [])
    if not isinstance(answers, list):
        raise RuntimeError("multipart migration found an invalid answer_key.questions")
    by_slot = {
        str(item.get("slotId")): item
        for item in answers
        if isinstance(item, dict) and item.get("slotId")
    }
    changed = False
    for section in sections:
        if not isinstance(section, dict) or not isinstance(
            section.get("questions"), list
        ):
            continue
        for question in section["questions"]:
            if not isinstance(question, dict) or question.get("qtype") not in _MULTIPART:
                continue
            slot_id = str(question.get("slotId") or "").strip()
            question_id = str(question.get("id") or "").strip()
            options = question.get("options")
            if not slot_id or not question_id or not isinstance(options, dict):
                raise RuntimeError(
                    "multipart migration found a question missing identity/options"
                )
            key = "sub_questions" if "sub_questions" in options else "subQuestions"
            items = options.get(key)
            if not isinstance(items, list) or not items:
                raise RuntimeError("multipart migration found an invalid sub-question list")
            entry = by_slot.get(slot_id)
            if entry is None:
                entry = {
                    "questionId": question_id,
                    "slotId": slot_id,
                    "answer": question.get("answer"),
                    "rubric": question.get("rubric"),
                }
                answers.append(entry)
                by_slot[slot_id] = entry
            existing_items = entry.get("subQuestions")
            existing_by_id = (
                {
                    str(item.get("subQuestionId")): item
                    for item in existing_items
                    if isinstance(item, dict) and item.get("subQuestionId")
                }
                if isinstance(existing_items, list)
                else {}
            )
            key_items: list[dict[str, Any]] = []
            safe_items: list[dict[str, Any]] = []
            for position, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    raise RuntimeError("multipart migration found a non-object sub-question")
                subquestion_id = _subquestion_id(slot_id, item, position)
                existing = existing_by_id.get(subquestion_id, {})
                sub_answer = existing.get("answer", item.get("answer"))
                if not isinstance(sub_answer, dict) or not sub_answer:
                    raise RuntimeError(
                        f"multipart migration would lose answer for {subquestion_id}"
                    )
                key_items.append(
                    {
                        "subQuestionId": subquestion_id,
                        "position": position,
                        "answer": copy.deepcopy(sub_answer),
                        "rubric": copy.deepcopy(
                            existing.get("rubric", item.get("rubric"))
                        ),
                    }
                )
                safe_item = copy.deepcopy(item)
                safe_item["subQuestionId"] = subquestion_id
                safe_item.pop("answer", None)
                safe_item.pop("rubric", None)
                safe_items.append(safe_item)
            entry["subQuestions"] = key_items
            options[key] = safe_items
            changed = True
    return content, answer_key, changed


def _merge_multipart_answers(
    content_value: Any, answer_key_value: Any
) -> tuple[Any, Any, bool]:
    content = copy.deepcopy(content_value)
    answer_key = copy.deepcopy(answer_key_value)
    if not isinstance(content, dict) or not isinstance(answer_key, dict):
        return content_value, answer_key_value, False
    answers = answer_key.get("questions")
    if not isinstance(answers, list):
        return content_value, answer_key_value, False
    by_slot = {
        str(item.get("slotId")): item
        for item in answers
        if isinstance(item, dict) and item.get("slotId")
    }
    changed = False
    for section in content.get("sections", []):
        if not isinstance(section, dict):
            continue
        for question in section.get("questions", []):
            if not isinstance(question, dict) or question.get("qtype") not in _MULTIPART:
                continue
            entry = by_slot.get(str(question.get("slotId")))
            options = question.get("options")
            if not isinstance(entry, dict) or not isinstance(options, dict):
                continue
            key_items = entry.pop("subQuestions", None)
            if not isinstance(key_items, list):
                continue
            key_by_id = {
                str(item.get("subQuestionId")): item
                for item in key_items
                if isinstance(item, dict) and item.get("subQuestionId")
            }
            key = "sub_questions" if "sub_questions" in options else "subQuestions"
            for position, item in enumerate(options.get(key, []), start=1):
                if not isinstance(item, dict):
                    continue
                answer_item = key_by_id.get(
                    _subquestion_id(str(question.get("slotId")), item, position)
                )
                if answer_item is not None:
                    item["answer"] = copy.deepcopy(answer_item.get("answer"))
                    if answer_item.get("rubric") is not None:
                        item["rubric"] = copy.deepcopy(answer_item.get("rubric"))
            changed = True
    return content, answer_key, changed


def _rewrite_versions(transform) -> set[Any]:
    bind = op.get_bind()
    versions = _paper_versions()
    changed_ids: set[Any] = set()
    statement = sa.select(versions.c.id, versions.c.content, versions.c.answer_key)
    for row in bind.execute(statement):
        content, answer_key, changed = transform(row.content, row.answer_key)
        if changed:
            bind.execute(
                sa.update(versions)
                .where(versions.c.id == row.id)
                .values(content=content, answer_key=answer_key)
            )
            changed_ids.add(row.id)
    return changed_ids


def upgrade() -> None:
    affected_versions = _rewrite_versions(_split_multipart_answers)
    if affected_versions:
        artifacts = _export_artifacts()
        op.get_bind().execute(
            sa.update(artifacts)
            .where(
                artifacts.c.paper_version_id.in_(affected_versions),
                artifacts.c.artifact_type == "answer_key",
                artifacts.c.status == "done",
            )
            .values(status="failed", error=_STALE_ARTIFACT_ERROR)
        )
    op.create_index(
        "uq_approval_request_events_one_withdrawal",
        "approval_request_events",
        ["approval_request_id"],
        unique=True,
        postgresql_where=sa.text("action = 'approval_withdrawn'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_approval_request_events_one_withdrawal",
        table_name="approval_request_events",
    )
    affected_versions = _rewrite_versions(_merge_multipart_answers)
    if affected_versions:
        artifacts = _export_artifacts()
        op.get_bind().execute(
            sa.update(artifacts)
            .where(
                artifacts.c.paper_version_id.in_(affected_versions),
                artifacts.c.artifact_type == "answer_key",
                artifacts.c.status == "failed",
                artifacts.c.error == _STALE_ARTIFACT_ERROR,
            )
            .values(status="done", error=None)
        )
