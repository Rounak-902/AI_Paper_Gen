"""Validation rules for incomplete textbook-extraction drafts."""

import pytest
from pydantic import ValidationError

from app.modules.questions.schemas import QuestionCreateIn


@pytest.mark.parametrize(
    "qtype",
    ["short_answer", "long_answer", "very_short_answer", "numerical", "diagram_based"],
)
def test_explicit_answer_review_flag_allows_unanswered_draft(qtype: str) -> None:
    question = QuestionCreateIn(
        qtype=qtype,
        marks="2",
        difficulty="medium",
        language="en",
        source="intext",
        stem="Textbook question awaiting a verified answer",
        meta={
            "answer_review_required": True,
            "quality_review_required": True,
        },
    )

    assert question.answer is None
    assert question.meta["answer_review_required"] is True


@pytest.mark.parametrize(
    "qtype",
    ["short_answer", "long_answer", "very_short_answer", "numerical", "diagram_based"],
)
def test_missing_answer_remains_invalid_without_review_flag(qtype: str) -> None:
    with pytest.raises(ValidationError, match="answer must be"):
        QuestionCreateIn(
            qtype=qtype,
            marks="2",
            difficulty="medium",
            language="en",
            source="intext",
            stem="Question without an answer or review flag",
        )


def test_review_flag_does_not_relax_a_malformed_present_answer() -> None:
    with pytest.raises(ValidationError, match="numerical answer must be"):
        QuestionCreateIn(
            qtype="numerical",
            marks="2",
            difficulty="medium",
            language="en",
            source="intext",
            stem="Numerical question with the wrong answer envelope",
            answer={"text": "42"},
            meta={"answer_review_required": True},
        )
