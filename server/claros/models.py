"""Shared Claros models. Authoritative contract; see docs/CONTRACTS.md.

snake_case on the wire. Keep web/src/lib/contracts.ts in sync.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Lang = str  # BCP-47-ish: "en", "de", "fr", "es", "ru"
Mode = Literal["capture", "debrief", "learn", "request"]
Role = Literal["expert", "learner", "admin"]


class User(BaseModel):
    id: str
    name: str
    role: Role


class Moment(BaseModel):
    """A pointer back to the screen + words that justify something."""
    session_id: str
    keyframe_ids: list[str] = Field(default_factory=list)
    t: float  # ms on the session clock
    utterance_ids: list[str] = Field(default_factory=list)


# ---------- perception ----------

class Field_(BaseModel):
    label: str                      # as shown on screen, any language
    value: Optional[str] = None     # raw text as shown
    canonical: Optional[str] = None  # e.g. "line.cost_center" once mapped
    normalized: Optional[Any] = None  # locale-normalized (numbers, dates)
    bbox: Optional[list[int]] = None  # [x, y, w, h] in keyframe pixels


class ScreenState(BaseModel):
    seq: int
    t: float
    app: Optional[str] = None        # "ERPNext", "Zammad", ...
    view: Optional[str] = None       # "Purchase Invoice form", "Ticket list"
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    status: Optional[str] = None     # "Draft", "Submitted", ...
    fields: list[Field_] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    dialogs: list[str] = Field(default_factory=list)
    toasts: list[str] = Field(default_factory=list)
    ui_lang: Optional[Lang] = None
    confidence: float = 0.0
    keyframe_id: Optional[str] = None


EventKind = Literal[
    "open", "navigate", "edit", "select", "save", "submit", "hold",
    "escalate", "approve", "reject", "dialog", "undo", "other",
]
ValueSource = Literal["typed", "pasted", "system", "unknown"]


class ScreenEvent(BaseModel):
    id: str
    seq: int
    t: float
    kind: EventKind
    app: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    field: Optional[str] = None
    canonical: Optional[str] = None
    old: Optional[str] = None
    new: Optional[str] = None
    source: ValueSource = "unknown"
    keyframe_id: Optional[str] = None
    confidence: float = 0.0
    summary: str = ""  # human-readable, e.g. "Cost center changed 4711 → 0400 on invoice 4471"


EventClass = Literal["routine", "judgment", "guardrail", "slip", "habit"]


# ---------- ledger ----------

UnknownType = Literal["why", "limit", "stop_and_ask", "never", "deliberate", "conflict", "coverage"]
KnowledgeScope = Literal["universal", "app", "occupation", "company", "personal_judgment"]
UnknownStatus = Literal["open", "asked", "answered", "resolved", "deferred", "dropped"]


class ExtractedRule(BaseModel):
    condition: Optional[str] = None
    threshold: Optional[str] = None
    action: Optional[str] = None
    escalate_to: Optional[str] = None


class Unknown(BaseModel):
    id: str
    type: UnknownType
    scope: KnowledgeScope = "personal_judgment"
    about_event_ids: list[str] = Field(default_factory=list)
    entity: Optional[str] = None
    hypothesis: Optional[str] = None       # if confident, ask to confirm instead of open why
    hypothesis_confidence: float = 0.0
    spoken_question: Optional[str] = None  # pre-generated, ≤15 words, in session language
    priority: float = 0.0
    status: UnknownStatus = "open"
    created_t: float = 0.0
    expires_t: Optional[float] = None
    moment: Optional[Moment] = None
    answer_utterance_ids: list[str] = Field(default_factory=list)
    resolution: Optional[str] = None       # text of the answer or the tool-found context
    resolution_source: Optional[str] = None  # "expert", "app_docs:<url>", "onet:<code>", "llm"
    extracted_rule: Optional[ExtractedRule] = None


# ---------- knowledge ----------

class Quote(BaseModel):
    id: str
    speaker: str
    speaker_id: str
    lang: Lang
    text: str
    translations: dict[Lang, str] = Field(default_factory=dict)
    t: float
    session_id: str
    source: Literal["live", "debrief", "doc", "inbox"] = "live"
    audio_clip: Optional[str] = None  # path; only if consent and no PII in clip


class ContextNote(BaseModel):
    """Non-expert context Claros found itself (cited)."""
    id: str
    text: str
    scope: KnowledgeScope
    source: str  # url, "onet:43-3031.00", "llm"


class Guardrail(BaseModel):
    id: str
    text: str
    quote_ids: list[str] = Field(default_factory=list)
    predicate: Optional[dict[str, Any]] = None  # json-logic over canonical vars; None if fuzzy
    fuzzy: bool = False
    action: Literal["block_and_explain", "warn", "stop_and_ask", "hold"] = "block_and_explain"
    owner: Optional[str] = None  # who to ask
    evidence: list[Moment] = Field(default_factory=list)
    experts: list[str] = Field(default_factory=list)  # attribution (merge)
    approved: bool = False


class Decision(BaseModel):
    kind: Literal["judgment", "routine"]
    description: str
    from_value: Optional[str] = None
    to_value: Optional[str] = None
    reason_quote_ids: list[str] = Field(default_factory=list)
    counterfactual: Optional[str] = None  # what would change the decision


class Variant(BaseModel):
    expert_id: str
    description: str
    reason_quote_ids: list[str] = Field(default_factory=list)


class Step(BaseModel):
    id: str
    order: int
    after: list[str] = Field(default_factory=list)  # partial order
    title: str
    state_signature: dict[str, Optional[str]] = Field(default_factory=dict)  # app/view/entity_type
    moment: Optional[Moment] = None
    decision: Optional[Decision] = None
    guardrail_ids: list[str] = Field(default_factory=list)
    context_note_ids: list[str] = Field(default_factory=list)
    experts: list[str] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    conflict: Optional[str] = None  # unresolved disagreement description
    approved: bool = False


class Coverage(BaseModel):
    status: Literal["missing", "partial", "ready"] = "missing"
    steps_with_evidence: float = 0.0
    judgments_complete: float = 0.0
    guardrails_complete: float = 0.0
    open_unknowns: int = 0
    conflicts: int = 0


class OnetMatch(BaseModel):
    occupation_code: str
    occupation_title: str
    task_id: Optional[str] = None
    task: Optional[str] = None
    dwas: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    score: float = 0.0


class ExamCase(BaseModel):
    variant: str
    predicted: str
    confidence: float
    expert_verdict: Optional[Literal["correct", "wrong"]] = None
    correction: Optional[str] = None


class WorkMap(BaseModel):
    id: str
    workflow_id: str
    version: int = 1
    name: str
    apps: list[str] = Field(default_factory=list)
    onet: Optional[OnetMatch] = None
    experts: list[User] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    guardrails: list[Guardrail] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    context_notes: list[ContextNote] = Field(default_factory=list)
    canonical_vars: dict[str, list[str]] = Field(default_factory=dict)  # canonical -> label aliases
    open_unknowns: list[Unknown] = Field(default_factory=list)
    exam: list[ExamCase] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    approved_by: list[str] = Field(default_factory=list)


class CaptureRequest(BaseModel):
    id: str
    workflow_hint: str
    requested_by: User
    moment: Optional[Moment] = None
    onet: Optional[OnetMatch] = None
    status: Literal["open", "accepted", "recorded", "done"] = "open"
    created_at: float = 0.0


class MasteryNode(BaseModel):
    step_id: str
    level: Literal["unseen", "caught", "hinted", "unaided"] = "unseen"
    attempts: int = 0


# ---------- intents ----------

LearnerIntent = Literal[
    "walk_through", "what_next", "why_this", "check_my_work", "hint", "just_watch",
    "stop", "ask_expert", "answer_prediction", "off_topic",
]
ExpertIntent = Literal[
    "narration", "answer", "correction", "confirm", "not_now", "off_record",
    "strike_that", "question_to_claros", "end_session",
]


class IntentResult(BaseModel):
    intent: str
    confidence: float
    backend: str  # "rules", "systemone:<model>", "llm"
