"""R-27 regression coverage for immutable multipart paper versions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.assembly.projection import project_version_pair
from app.modules.questions.schemas import QuestionCreateIn


@pytest.mark.parametrize("qtype", ["case_study", "source_based"])
def test_multipart_projection_separates_answers_from_student_content(qtype: str) -> None:
    question = {
        "id": "question-1",
        "slotId": "section-a:1",
        "sectionId": "section-a",
        "qtype": qtype,
        "stem": "Read the source.",
        "marks": "3",
        "answer": None,
        "rubric": None,
        "options": {
            "sub_questions": [
                {
                    "stem": "First?",
                    "marks": "1",
                    "answer": {"text": "Secret one"},
                    "rubric": {"points": ["One"]},
                },
                {
                    "stem": "Second?",
                    "marks": "2",
                    "answer": {"text": "Secret two"},
                    "rubric": None,
                },
            ]
        },
    }

    content, answer = project_version_pair(question)

    content_items = content["options"]["sub_questions"]
    assert content_items == [
        {
            "subQuestionId": "section-a:1:sub-1",
            "stem": "First?",
            "marks": "1",
        },
        {
            "subQuestionId": "section-a:1:sub-2",
            "stem": "Second?",
            "marks": "2",
        },
    ]
    assert answer["answer"] is None
    assert answer["subQuestions"] == [
        {
            "subQuestionId": "section-a:1:sub-1",
            "position": 1,
            "answer": {"text": "Secret one"},
            "rubric": {"points": ["One"]},
        },
        {
            "subQuestionId": "section-a:1:sub-2",
            "position": 2,
            "answer": {"text": "Secret two"},
            "rubric": None,
        },
    ]
    assert question["options"]["sub_questions"][0]["answer"] == {
        "text": "Secret one"
    }


def test_question_schema_rejects_empty_nested_answer_before_assembly() -> None:
    with pytest.raises(ValidationError, match="sub-question answer must not be empty"):
        QuestionCreateIn(
            qtype="case_study",
            marks="2",
            difficulty="medium",
            stem="Read the source.",
            options={
                "sub_questions": [
                    {"stem": "First?", "marks": "1", "answer": {}},
                    {"stem": "Second?", "marks": "1", "answer": {"text": "Two"}},
                ]
            },
        )
