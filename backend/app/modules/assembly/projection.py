"""Project question-bank records into immutable paper and answer-key snapshots.

Multipart questions keep their authored answers beside each sub-question in the
question bank.  A paper version must split that source record into student-safe
content and a separately authorized answer-key entry.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any


MULTIPART_QUESTION_TYPES = frozenset({"case_study", "source_based"})


class AnswerProjectionError(ValueError):
    """The source question cannot produce a complete immutable answer key."""


def _multipart_items(question: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    options = question.get("options")
    if not isinstance(options, Mapping):
        raise AnswerProjectionError("multipart question options must be an object")
    items = options.get("sub_questions") or options.get("subQuestions")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise AnswerProjectionError("multipart question sub_questions must be a list")
    if not items:
        raise AnswerProjectionError("multipart question must contain sub-questions")
    if not all(isinstance(item, Mapping) for item in items):
        raise AnswerProjectionError("multipart sub-questions must be objects")
    return list(items)


def _subquestion_id(slot_id: str, item: Mapping[str, Any], position: int) -> str:
    existing = item.get("subQuestionId") or item.get("id")
    return str(existing) if existing else f"{slot_id}:sub-{position}"


def project_version_question(question: Mapping[str, Any]) -> dict[str, Any]:
    """Return a student-safe question snapshot for ``PaperVersion.content``."""

    projected = copy.deepcopy(dict(question))
    if str(projected.get("qtype")) not in MULTIPART_QUESTION_TYPES:
        return projected

    slot_id = str(projected.get("slotId") or "").strip()
    if not slot_id:
        raise AnswerProjectionError("multipart question is missing slotId")
    options = projected.get("options")
    if not isinstance(options, dict):
        raise AnswerProjectionError("multipart question options must be an object")
    key = "sub_questions" if "sub_questions" in options else "subQuestions"
    sanitized: list[dict[str, Any]] = []
    for position, item in enumerate(_multipart_items(projected), start=1):
        value = copy.deepcopy(dict(item))
        value["subQuestionId"] = _subquestion_id(slot_id, item, position)
        value.pop("answer", None)
        value.pop("rubric", None)
        sanitized.append(value)
    options[key] = sanitized
    return projected


def project_answer_entry(question: Mapping[str, Any]) -> dict[str, Any]:
    """Return the separately authorized answer entry for one question snapshot."""

    question_id = str(question.get("id") or "").strip()
    slot_id = str(question.get("slotId") or "").strip()
    if not question_id or not slot_id:
        raise AnswerProjectionError("question is missing id or slotId")
    entry: dict[str, Any] = {
        "questionId": question_id,
        "slotId": slot_id,
        "answer": copy.deepcopy(question.get("answer")),
        "rubric": copy.deepcopy(question.get("rubric")),
    }
    if str(question.get("qtype")) not in MULTIPART_QUESTION_TYPES:
        return entry

    subquestions: list[dict[str, Any]] = []
    for position, item in enumerate(_multipart_items(question), start=1):
        answer = item.get("answer")
        if not isinstance(answer, Mapping) or not answer:
            raise AnswerProjectionError(
                f"multipart sub-question {position} must have a non-empty answer object"
            )
        subquestions.append(
            {
                "subQuestionId": _subquestion_id(slot_id, item, position),
                "position": position,
                "answer": copy.deepcopy(dict(answer)),
                "rubric": copy.deepcopy(item.get("rubric")),
            }
        )
    entry["subQuestions"] = subquestions
    return entry


def project_version_pair(
    question: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split one bank/checkpoint snapshot into content and answer-key records."""

    return project_version_question(question), project_answer_entry(question)


__all__ = [
    "AnswerProjectionError",
    "MULTIPART_QUESTION_TYPES",
    "project_answer_entry",
    "project_version_pair",
    "project_version_question",
]
