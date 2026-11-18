"""Global curriculum catalog module (P2-T1).

Authenticated requests carry an organization context through the normal guard and
transaction chain, while catalog rows remain shared across organizations as required by
the RLS-exempt schema.
"""

from app.modules.curriculum.repository import (
    BoardRepository,
    ChapterLineageCycleError,
    ChapterRepository,
    ClassRepository,
    CurriculumVersionRepository,
    LearningOutcomeRepository,
    OutcomeRepository,
    SubjectRepository,
)

__all__ = [
    "BoardRepository",
    "ChapterLineageCycleError",
    "ChapterRepository",
    "ClassRepository",
    "CurriculumVersionRepository",
    "LearningOutcomeRepository",
    "OutcomeRepository",
    "SubjectRepository",
]
