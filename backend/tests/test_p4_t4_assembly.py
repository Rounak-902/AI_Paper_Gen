"""Focused contract proofs for P4-T4 assembly orchestration."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.assembly.service import PaperAssemblyError, PaperAssemblyService
from app.workers.idempotency import IdempotentTask
from app.workers.tasks import assemble_paper
from packages.domain.assembler import Compromise, CoverageFact


def test_celery_entry_uses_p4_t1_idempotent_late_ack_envelope() -> None:
    assert isinstance(assemble_paper, IdempotentTask)
    assert assemble_paper.name == "papergen.workers.assemble_paper"
    assert assemble_paper.acks_late is True
    assert assemble_paper.reject_on_worker_lost is True


def test_assembly_reuses_solver_without_a_second_cp_sat_model() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "modules"
        / "assembly"
        / "service.py"
    ).read_text(encoding="utf-8")
    assert "CpSatFeasibilitySolver" in source
    assert "blueprint_from_request" in source
    assert "candidate_from_question" in source
    assert "cp_model" not in source


def test_paper_checks_consume_solver_facts_and_accept_named_compromise() -> None:
    result = SimpleNamespace(
        coverage=(
            CoverageFact(
                dimension="difficulty",
                target="hard",
                target_value="20% +/- 0pp",
                actual="10.0%",
                satisfied=False,
            ),
            CoverageFact(
                dimension="textbook_floor",
                target="textbook+intext",
                target_value=">=70%",
                actual="70.0%",
                satisfied=True,
            ),
        ),
        compromises=(
            Compromise(
                tier="difficulty",
                constraint="difficulty:hard",
                target="20% +/- 0pp",
                actual="10.0%",
                deviation="10.0% beyond tolerance",
                reason="thin pool",
            ),
        ),
    )
    checks = PaperAssemblyService._paper_checks(
        payload=SimpleNamespace(total_marks=Decimal("4.0")),
        solve_result=result,
        snapshots=[{"marks": "2.0"}, {"marks": "2.0"}],
        selected_ids=["q1", "q2"],
        duplicate_pair_count=0,
    )
    assert checks["difficultyBalance"]["source"] == "solver-coverage-facts"
    assert checks["difficultyBalance"]["passedOrReported"] is True
    assert checks["mixedModeTextbookFloor"]["passedOrReported"] is True
    assert checks["marksReconciliation"]["passed"] is True
    assert checks["noInPaperDuplicates"]["passed"] is True


def test_paper_checks_reject_p2_duplicate_pairs() -> None:
    result = SimpleNamespace(coverage=(), compromises=())
    with pytest.raises(PaperAssemblyError, match="paper-level checks failed"):
        PaperAssemblyService._paper_checks(
            payload=SimpleNamespace(total_marks=Decimal("2.0")),
            solve_result=result,
            snapshots=[{"marks": "2.0"}],
            selected_ids=["q1"],
            duplicate_pair_count=1,
        )
