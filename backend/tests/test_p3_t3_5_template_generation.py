"""P3-T3.5 deterministic template engine and real persistence boundary."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text

from app.common.deps import RequestScope
from app.models import QuestionBank
from app.modules.ai.providers.gemini import GeminiAdapter
from app.modules.questions import schemas, service
from app.modules.questions.template_service import (
    build_template_question_payload,
    generate_and_create_question,
)
from packages.prompts.templates import (
    InvalidTemplateCatalogError,
    TemplateCatalogStore,
    TemplateEngine,
    TemplateParameterError,
)


TEMPLATE_REFS = (
    "linear_evaluation/v1", "rectangle_area/v1", "distance_travelled/v1",
    "addition_blank/v1", "subtraction_blank/v1", "percentage_blank/v1",
    "product_one_word/v1", "quotient_one_word/v1", "remainder_one_word/v1",
    "addition_mcq/v1", "rectangle_perimeter_mcq/v1", "integer_power_mcq/v1",
    "addition_true_false/v1", "multiplication_true_false/v1", "square_true_false/v1",
)


def _independent_value(answer_function: str, p: dict[str, int | str]) -> int | float | bool:
    if answer_function == "linear_expression":
        return int(p["a"]) * int(p["x"]) + int(p["b"])
    if answer_function == "rectangle_area":
        return int(p["length"]) * int(p["width"])
    if answer_function == "distance":
        return int(p["speed"]) * int(p["time"])
    if answer_function == "sum":
        return int(p["a"]) + int(p["b"])
    if answer_function == "difference":
        return int(p["minuend"]) - int(p["subtrahend"])
    if answer_function == "percentage":
        return int(p["percent"]) * int(p["whole"]) / 100
    if answer_function == "product":
        return int(p["a"]) * int(p["b"])
    if answer_function == "quotient":
        return int(p["dividend"]) // int(p["divisor"])
    if answer_function == "remainder":
        return int(p["dividend"]) % int(p["divisor"])
    if answer_function == "perimeter":
        return 2 * (int(p["length"]) + int(p["width"]))
    if answer_function == "power":
        return int(p["base"]) ** int(p["exponent"])
    if answer_function in {"sum_claim", "product_claim", "square_claim"}:
        return int(p["delta"]) == 0
    raise AssertionError(f"verification does not recognize {answer_function}")


def _assert_answer(question) -> None:
    expected = _independent_value(question.answer_function, dict(question.parameters))
    if question.qtype == "numerical":
        assert question.answer["value"] == expected
    elif question.qtype == "fill_blank":
        assert question.answer["blanks"] == [str(int(expected) if float(expected).is_integer() else expected)]
    elif question.qtype == "one_word":
        assert question.answer["value"] == str(expected)
    elif question.qtype == "true_false":
        assert question.answer["value"] is expected
    elif question.qtype == "mcq":
        index = question.answer["index"]
        assert question.options[index]["is_correct"] is True
        assert int(question.options[index]["text"]) == expected
        assert sum(option["is_correct"] for option in question.options) == 1


def test_catalog_has_three_distinct_templates_for_every_supported_type() -> None:
    catalog = TemplateCatalogStore().load_catalog()
    counts = Counter(template.qtype for template in catalog.templates)
    assert counts == {
        "numerical": 3,
        "fill_blank": 3,
        "one_word": 3,
        "mcq": 3,
        "true_false": 3,
    }
    assert tuple(template.template_ref for template in catalog.templates) == TEMPLATE_REFS


@pytest.mark.parametrize("template_ref", TEMPLATE_REFS)
def test_multiple_random_instances_are_independently_correct_and_p2_valid(
    template_ref: str,
) -> None:
    engine = TemplateEngine()
    seen_parameters = set()
    for seed in (101, 202, 303, 404):
        generated = engine.generate(template_ref, seed=seed)
        seen_parameters.add(tuple(sorted(generated.parameters.items())))
        _assert_answer(generated)
        payload = build_template_question_payload(generated)
        assert isinstance(payload, schemas.QuestionCreateIn)
        assert payload.source == "template_generated"
        assert payload.prompt_version_id is None
        assert payload.meta["llm_calls"] == 0
    assert len(seen_parameters) >= 2


def test_seed_is_reproducible_and_exact_overrides_are_type_checked() -> None:
    engine = TemplateEngine()
    assert engine.generate("linear_evaluation/v1", seed=8472) == engine.generate(
        "linear_evaluation/v1", seed=8472
    )
    fixed = engine.generate(
        "subtraction_blank/v1",
        parameters={"minuend": 45, "subtrahend": 17},
    )
    assert fixed.stem == "Complete the equation: 45 - 17 = ___."
    assert fixed.answer == {"blanks": ["28"]}
    with pytest.raises(TemplateParameterError, match="constraints"):
        engine.generate(
            "subtraction_blank/v1",
            parameters={"minuend": 45, "subtrahend": 50},
        )
    with pytest.raises(TemplateParameterError, match="typed"):
        engine.generate(
            "subtraction_blank/v1",
            parameters={"minuend": "45", "subtrahend": 17},
        )


def test_catalog_rejects_unreviewed_schema_drift(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "packages" / "prompts" / "templates" / "catalog" / "v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["templates"][0]["placeholders"]["a"]["type"] = "python_expression"
    destination = tmp_path / "v1.json"
    destination.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InvalidTemplateCatalogError, match="integer or choice"):
        TemplateCatalogStore(destination).load_catalog()


def test_generation_path_makes_zero_gemini_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    generate = AsyncMock(side_effect=AssertionError("Gemini generate must not be called"))
    classify = AsyncMock(side_effect=AssertionError("Gemini classify must not be called"))
    embed = AsyncMock(side_effect=AssertionError("Gemini embed must not be called"))
    monkeypatch.setattr(GeminiAdapter, "generate", generate)
    monkeypatch.setattr(GeminiAdapter, "classify", classify)
    monkeypatch.setattr(GeminiAdapter, "embed", embed)
    engine = TemplateEngine()
    for index, template_ref in enumerate(TEMPLATE_REFS):
        generated = engine.generate(template_ref, seed=9000 + index)
        build_template_question_payload(generated)
    assert generate.await_count == classify.await_count == embed.await_count == 0


@pytest.mark.db
async def test_real_create_path_persists_source_and_surfaces_near_duplicate(
    db_session, tenant, curriculum
) -> None:
    request_scope = RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="coaching_admin",
    )
    existing = schemas.QuestionCreateIn(
        qtype="fill_blank",
        marks=Decimal("1.0"),
        difficulty="easy",
        bloom="remember",
        language="en",
        source="institute_bank",
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        stem="Complete the equation: 47 + 28 = ___",
        answer={"blanks": ["75"]},
    )
    existing_out = await service.create_question(
        session=db_session,
        scope=request_scope,
        org_id=tenant.org_id,
        payload=existing,
    )
    await db_session.commit()

    result = await generate_and_create_question(
        session=db_session,
        request_scope=request_scope,
        org_id=tenant.org_id,
        template_ref="addition_blank/v1",
        parameters={"a": 47, "b": 28},
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
    )
    await db_session.commit()

    assert result.question.source == "template_generated"
    assert result.question.prompt_version_id is None
    assert result.question.meta["template_ref"] == "addition_blank/v1"
    assert result.question.warnings[0].code == "NEAR_DUPLICATE"
    assert existing_out.id in {
        candidate.existing_question_id
        for candidate in result.question.warnings[0].candidates
    }

    db_session.expunge_all()
    stored = await db_session.scalar(
        select(QuestionBank).where(QuestionBank.id == result.question.id)
    )
    assert stored is not None
    assert stored.source == "template_generated"
    assert stored.answer == {"blanks": ["75"]}
    source_constraint = await db_session.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'question_bank'::regclass "
            "AND conname = 'ck_question_bank_source'"
        )
    )
    assert "template_generated" in source_constraint
