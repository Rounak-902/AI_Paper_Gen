"""Prompt-only blank convention against the unchanged question-bank counter."""
import pytest
from pydantic import ValidationError

from app.modules.questions.schemas import QuestionCreateIn
from packages.prompts import load_prompt


def test_fill_blank_prompt_specifies_exact_markers_and_ordered_answers():
    prompt = load_prompt("generate_question/v1")
    instruction = prompt.layers.developer
    assert "For fill_blank only" in instruction
    assert "exactly the literal three-underscore substring ___" in instruction
    assert "multiple separate ___ occurrences" in instruction
    assert "exactly one answer per occurrence in left-to-right order" in instruction
    assert "______ and _______ each contain two counted ___ substrings" in instruction


@pytest.mark.parametrize("stem,answers,count", [
    ("The width is ___.", ["4"], 1),
    ("The length is ___ and the width is ___.", ["7", "4"], 2),
])
def test_canonical_prompt_examples_match_existing_bank_counter(stem, answers, count):
    question = QuestionCreateIn(qtype="fill_blank", marks="1", difficulty="easy", language="en",
                                stem=stem, answer={"blanks": answers})
    assert question.stem.count("___") == len(question.answer["blanks"]) == count


@pytest.mark.parametrize("marker", ["______", "_______"])
def test_long_underscore_run_is_not_one_blank_under_existing_contract(marker):
    with pytest.raises(ValidationError, match="stem has 2 blank"):
        QuestionCreateIn(qtype="fill_blank", marks="1", difficulty="easy", language="en",
                         stem="The width is " + marker + ".", answer={"blanks": ["4"]})
