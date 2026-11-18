"""PaperGen ORM models.

**Importing this package is what makes Alembic autogenerate work.** ``Base.metadata``
only knows about a table if the module defining it has been imported, so a model
that is not re-exported here is invisible to ``alembic revision --autogenerate``
and, worse, autogenerate will emit a ``drop_table`` for it. Every new model must be
added below.

Scope after P1-T4 — doc 03 §9 steps 2-19, the complete schema:

===========================  ==================  =================================
Table group (doc 03 §4)      Migration           Models
===========================  ==================  =================================
Identity and tenancy         0002, 0018, 0026    Organization, Branch, User,
                                                 Membership, Invitation
Curriculum                   0003, 0004          Board, Class, Subject, Chapter,
                                                 LearningOutcome, CurriculumVersion
Programs                     0005                Program, ProgramSubject,
                                                 ProgramTeacher
Questions                    0006, 0018, 0019    QuestionBank
Textbooks                    0008                Textbook, TextbookAsset
Papers                       0009, 0010, 0011    Template, GeneratedPaper,
                                                 PaperVersion, Approval
Billing and metering         0012, 0013          Subscription, Usage
Audit and analytics          0014, 0015          AuditLog, AnalyticsDaily
AI operations                0016                PromptVersion
Notifications                0017, 0040          Notification,
                                                 NotificationEventReceipt,
                                                 NotificationDigest
===========================  ==================  =================================

0018 appears twice on purpose. doc 03 §9 step 18 is "Add indexes, constraints, RLS
policies, partitions, and seed data", and two groups collect work from it: it adds the
search artifacts to ``question_bank`` (the generated ``tsvector`` column and the four GIN
indexes), *and* it adds the ``organizations.subscription_id`` foreign key that revision
0002 deliberately shipped without, completing the one expand-migrate-contract sequence in
the chain (doc 03 §8). 0019 then backfills ``question_bank``'s lineage pointers, which is
why Questions names three revisions. 0007 is a documented no-op — see below.

Nothing in this chain creates an RLS policy or a partition, both of which step 18 also
names; revision 0018's docstring says why for each, and RLS in particular is P1-T5's.

Not here, on purpose:

* ``QuestionEmbedding`` — doc 03 §8 says it "is defined but its migration is gated on
  pgvector adoption (ADR-007)". Defining the model without the table is not actually
  available to us: ``Base.metadata`` would contain a table the database does not have,
  and ``alembic check`` — the objective proof that the hand-written revisions match
  the models — would report permanent drift. Excluding it via ``include_object`` in
  ``env.py`` would weaken that check for every future table too. So the model is
  omitted along with the table, and revision 0007 stands as the documented no-op that
  keeps revision numbers aligned with doc 03 §9's nineteen steps. Flagged in the
  P1-T4 hand-off notes: doc 03 §8's instruction and doc 03 §5's "**not created at
  MVP**" cannot both be satisfied.
* ``invitations`` — **no longer absent as of P1-T8.** P1-T3 and P1-T4 both declined to
  invent it (doc 03 has no §5 section for it and ADR-012 §3 models the invite flow
  through ``memberships.status = 'invited'``). P1-T8 has to implement an accept flow
  that *creates the user*, and ``memberships.user_id`` is NOT NULL, so an invite to an
  address with no account cannot be a ``memberships`` row. The table is added in
  revision 0026 and the override of ADR-012 is argued in full in
  ``app/models/invitation.py``'s module docstring. When the invited address already
  has a user row, the ``memberships`` row is *also* written with
  ``status = 'invited'``, so ADR-012's model still holds wherever it can.
* A ``competencies`` table. ``question_bank.competency_ids`` references it and doc 03
  defines it nowhere — not in §2, §5 or §8. The column ships because §5 requires it;
  the absent table is reported rather than invented.
* Anything that is not a table or a column: no repository base class, no RLS policies,
  no auth. P1-T4 is schema only (P1-T5 and P1-T7 own those).
"""

from app.db.base import Base
from app.models.approval import (
    APPROVAL_EVENT_ACTIONS,
    APPROVAL_REQUEST_STATUSES,
    ApprovalComment,
    ApprovalRequest,
    ApprovalRequestEvent,
)
from app.models.audit import AnalyticsDaily, AuditLog
from app.models.assembly import (
    ASSEMBLY_RUN_STATUSES,
    ASSEMBLY_SLOT_STATUSES,
    OUTBOX_STATUSES,
    PaperAssemblyRun,
    PaperAssemblySlot,
    TaskOutbox,
)
from app.models.billing import (
    SUBSCRIPTION_PLANS,
    SUBSCRIPTION_STATUSES,
    USAGE_METRICS,
    Subscription,
    Usage,
)
from app.models.branch import BRANCH_STATUSES, Branch
from app.models.curriculum import (
    CURRICULUM_VERSION_STATUSES,
    Board,
    Chapter,
    Class,
    CurriculumVersion,
    LearningOutcome,
    Subject,
)
from app.models.export import EXPORT_FORMATS, EXPORT_STATUSES, ExportArtifact
from app.models.invitation import (
    INVITATION_OPEN_STATUS,
    INVITATION_STATUSES,
    Invitation,
)
from app.models.membership import (
    MEMBERSHIP_ROLES,
    MEMBERSHIP_STATUSES,
    RESERVED_MEMBERSHIP_ROLES,
    Membership,
)
from app.models.notification import (
    NOTIFICATION_CHANNELS,
    NOTIFICATION_CODES,
    NOTIFICATION_DIGEST_STATUSES,
    NOTIFICATION_EVENT_SOURCES,
    NOTIFICATION_EVENT_STATUSES,
    NOTIFICATION_STATUSES,
    Notification,
    NotificationDigest,
    NotificationEventReceipt,
)
from app.models.organization import ORG_STATUSES, ORG_TYPES, Organization
from app.models.paper import (
    APPROVAL_ACTIONS,
    GENERATED_PAPER_STATUSES,
    Approval,
    GeneratedPaper,
    PaperVersion,
)
from app.models.paper_draft import PaperEditorDraft
from app.models.program import (
    PROGRAM_STATUSES,
    Program,
    ProgramSubject,
    ProgramTeacher,
)
from app.models.prompt import PROMPT_VERSION_STATUSES, PromptVersion
from app.models.question import (
    QUESTION_BLOOM_LEVELS,
    QUESTION_DIFFICULTIES,
    QUESTION_SCOPES,
    QUESTION_SOURCES,
    QUESTION_STATUSES,
    QUESTION_TYPES,
    QuestionBank,
)
from app.models.question_import import QUESTION_IMPORT_STATUSES, QuestionImportJob
from app.models.template import TEMPLATE_STATUSES, Template
from app.models.textbook import (
    TEXTBOOK_ASSET_KINDS,
    TEXTBOOK_STATUSES,
    Textbook,
    TextbookAsset,
)
from app.models.user import USER_STATUSES, User

__all__ = [
    "APPROVAL_EVENT_ACTIONS",
    "APPROVAL_REQUEST_STATUSES",
    "APPROVAL_ACTIONS",
    "ASSEMBLY_RUN_STATUSES",
    "ASSEMBLY_SLOT_STATUSES",
    "BRANCH_STATUSES",
    "CURRICULUM_VERSION_STATUSES",
    "EXPORT_FORMATS",
    "EXPORT_STATUSES",
    "GENERATED_PAPER_STATUSES",
    "INVITATION_OPEN_STATUS",
    "INVITATION_STATUSES",
    "MEMBERSHIP_ROLES",
    "MEMBERSHIP_STATUSES",
    "NOTIFICATION_CHANNELS",
    "NOTIFICATION_CODES",
    "NOTIFICATION_DIGEST_STATUSES",
    "NOTIFICATION_EVENT_SOURCES",
    "NOTIFICATION_EVENT_STATUSES",
    "NOTIFICATION_STATUSES",
    "ORG_STATUSES",
    "ORG_TYPES",
    "OUTBOX_STATUSES",
    "PROGRAM_STATUSES",
    "PROMPT_VERSION_STATUSES",
    "QUESTION_BLOOM_LEVELS",
    "QUESTION_DIFFICULTIES",
    "QUESTION_SCOPES",
    "QUESTION_SOURCES",
    "QUESTION_STATUSES",
    "QUESTION_TYPES",
    "QUESTION_IMPORT_STATUSES",
    "RESERVED_MEMBERSHIP_ROLES",
    "SUBSCRIPTION_PLANS",
    "SUBSCRIPTION_STATUSES",
    "TEMPLATE_STATUSES",
    "TEXTBOOK_ASSET_KINDS",
    "TEXTBOOK_STATUSES",
    "USAGE_METRICS",
    "USER_STATUSES",
    "AnalyticsDaily",
    "Approval",
    "ApprovalComment",
    "ApprovalRequest",
    "ApprovalRequestEvent",
    "AuditLog",
    "Base",
    "Board",
    "Branch",
    "Chapter",
    "Class",
    "CurriculumVersion",
    "GeneratedPaper",
    "ExportArtifact",
    "Invitation",
    "LearningOutcome",
    "Membership",
    "Notification",
    "NotificationDigest",
    "NotificationEventReceipt",
    "Organization",
    "PaperVersion",
    "PaperEditorDraft",
    "PaperAssemblyRun",
    "PaperAssemblySlot",
    "Program",
    "ProgramSubject",
    "ProgramTeacher",
    "PromptVersion",
    "QuestionBank",
    "QuestionImportJob",
    "Subject",
    "Subscription",
    "Template",
    "Textbook",
    "TextbookAsset",
    "TaskOutbox",
    "Usage",
    "User",
]
