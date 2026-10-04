/**
 * TS mirror of server/claros/models.py + WS message types (docs/CONTRACTS.md).
 * snake_case on the wire — field names identical to Python. Keep in sync by hand.
 */

export type Lang = string; // "en" | "de" | "fr" | "es" | "ru" ...
export type Mode = "capture" | "debrief" | "learn" | "request";
export type Role = "expert" | "learner" | "admin";

export interface User {
  id: string;
  name: string;
  role: Role;
}

export interface Moment {
  session_id: string;
  keyframe_ids: string[];
  t: number; // ms on the session clock
  utterance_ids: string[];
}

// ---------- perception ----------

export interface Field_ {
  label: string;
  value?: string | null;
  canonical?: string | null;
  normalized?: unknown;
  bbox?: number[] | null; // [x, y, w, h] keyframe px
}

export interface ScreenState {
  seq: number;
  t: number;
  app?: string | null;
  view?: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  status?: string | null;
  fields: Field_[];
  tables: Record<string, unknown>[];
  dialogs: string[];
  toasts: string[];
  ui_lang?: Lang | null;
  confidence: number;
  keyframe_id?: string | null;
}

export type EventKind =
  | "open" | "navigate" | "edit" | "select" | "save" | "submit" | "hold"
  | "escalate" | "approve" | "reject" | "dialog" | "undo" | "other";
export type ValueSource = "typed" | "pasted" | "system" | "unknown";

export interface ScreenEvent {
  id: string;
  seq: number;
  t: number;
  kind: EventKind;
  app?: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  field?: string | null;
  canonical?: string | null;
  old?: string | null;
  new?: string | null;
  source: ValueSource;
  keyframe_id?: string | null;
  confidence: number;
  summary: string;
}

export type EventClass = "routine" | "judgment" | "guardrail" | "slip" | "habit";

// ---------- ledger ----------

export type UnknownType = "why" | "limit" | "stop_and_ask" | "never" | "deliberate" | "conflict" | "coverage";
export type KnowledgeScope = "universal" | "app" | "occupation" | "company" | "personal_judgment";
export type UnknownStatus = "open" | "asked" | "answered" | "resolved" | "deferred" | "dropped";

export interface ExtractedRule {
  condition?: string | null;
  threshold?: string | null;
  action?: string | null;
  escalate_to?: string | null;
}

export interface Unknown {
  id: string;
  type: UnknownType;
  scope: KnowledgeScope;
  about_event_ids: string[];
  entity?: string | null;
  hypothesis?: string | null;
  hypothesis_confidence?: number; // default 0.0
  spoken_question?: string | null;
  priority: number;
  status: UnknownStatus;
  created_t: number;
  expires_t?: number | null;
  moment?: Moment | null;
  answer_utterance_ids: string[];
  resolution?: string | null;
  resolution_source?: string | null;
  extracted_rule?: ExtractedRule | null;
  mandatory?: boolean;
  meta?: Record<string, unknown>;
}

// ---------- knowledge ----------

export interface Quote {
  id: string;
  speaker: string;
  speaker_id: string;
  lang: Lang;
  text: string;
  translations: Record<Lang, string>;
  t: number;
  session_id: string;
  source: "live" | "debrief" | "doc" | "inbox";
  audio_clip?: string | null;
}

export interface ContextNote {
  id: string;
  text: string;
  scope: KnowledgeScope;
  source: string;
}

export interface Guardrail {
  id: string;
  text: string;
  quote_ids: string[];
  predicate?: Record<string, unknown> | null; // json-logic
  fuzzy: boolean;
  action: "block_and_explain" | "warn" | "stop_and_ask" | "hold";
  owner?: string | null;
  evidence: Moment[];
  experts: string[];
  approved: boolean;
}

export interface Decision {
  kind: "judgment" | "routine";
  description: string;
  from_value?: string | null;
  to_value?: string | null;
  reason_quote_ids: string[];
  counterfactual?: string | null;
}

export interface Variant {
  expert_id: string;
  description: string;
  reason_quote_ids: string[];
}

export interface Step {
  id: string;
  order: number;
  after: string[];
  title: string;
  state_signature: Record<string, string | null>;
  moment?: Moment | null;
  decision?: Decision | null;
  guardrail_ids: string[];
  context_note_ids: string[];
  experts: string[];
  variants: Variant[];
  conflict?: string | null;
  approved: boolean;
}

export interface Coverage {
  status: "missing" | "partial" | "ready";
  steps_with_evidence: number;
  judgments_complete: number;
  guardrails_complete: number;
  open_unknowns: number;
  conflicts: number;
}

export interface OnetMatch {
  occupation_code: string;
  occupation_title: string;
  task_id?: string | null;
  task?: string | null;
  dwas: string[];
  technologies: string[];
  score: number;
}

export interface ExamCase {
  variant: string;
  predicted: string;
  confidence: number;
  expert_verdict?: "correct" | "wrong" | null;
  correction?: string | null;
}

export interface WorkMap {
  id: string;
  workflow_id: string;
  version: number;
  name: string;
  apps: string[];
  onet?: OnetMatch | null;
  experts: User[];
  session_ids: string[];
  steps: Step[];
  guardrails: Guardrail[];
  quotes: Quote[];
  context_notes: ContextNote[];
  canonical_vars: Record<string, string[]>;
  open_unknowns: Unknown[];
  exam: ExamCase[];
  coverage: Coverage;
  approved_by: string[];
}

export interface CaptureRequest {
  id: string;
  workflow_hint: string;
  requested_by: User;
  moment?: Moment | null;
  onet?: OnetMatch | null;
  status: "open" | "accepted" | "recorded" | "done";
  created_at: number;
  workflow_id?: string | null;
  /** duplicate asks merge into one request */
  requested_by_all?: User[];
  count?: number;
  merged?: boolean;
}

export interface MasteryNode {
  step_id: string;
  level: "unseen" | "caught" | "hinted" | "unaided";
  attempts: number;
}

// ---------- intents ----------

export type LearnerIntent =
  | "walk_through" | "what_next" | "why_this" | "check_my_work" | "hint" | "just_watch"
  | "stop" | "ask_expert" | "answer_prediction" | "off_topic";
export type ExpertIntent =
  | "narration" | "answer" | "correction" | "confirm" | "not_now" | "off_record"
  | "strike_that" | "question_to_claros" | "end_session";

export interface IntentResult {
  intent: string;
  confidence: number;
  backend: string;
}

// ---------- REST ----------

export interface CreateSessionRequest {
  mode: Mode;
  user: User;
  lang: Lang;
  workflow_id?: string | null;
}
export interface CreateSessionResponse {
  session_id: string;
}
export interface LookupRequest {
  screen_state?: ScreenState | null;
  utterance: string;
  lang: Lang;
}
export interface LookupResponse {
  match?: { workflow_id: string; score: number; coverage: Coverage } | null;
  onet?: OnetMatch | null;
}

// ---------- WebSocket: client → server ----------

export type ActivityKind = "typing" | "scrolling" | "navigating" | "idle" | "away";
export type KeyframeReason = "settle" | "boundary" | "heartbeat" | "toast";
export type ControlAction = "off_record_on" | "off_record_off" | "strike_that" | "not_now" | "end_task" | "debrief_skip";
/** [x, y, w, h] in keyframe pixel coords */
export type Rect = [number, number, number, number];

export interface HelloMsg {
  type: "hello";
  session_id: string;
  mode: Mode;
  user: User;
  lang: Lang;
  workflow_id?: string | null;
  /** expert voice clips are kept only with explicit consent (capture start) */
  consent?: { voice_clips: boolean };
}
export interface ClockSyncOut {
  type: "clock_sync";
  client_t: number;
}
export interface ActivityMsg {
  type: "activity";
  t: number;
  kind: ActivityKind;
  tiles_changed: number;
  dims: [number, number];
}
export interface KeyframeMsg {
  type: "keyframe";
  t: number;
  seq: number;
  reason: KeyframeReason;
  jpeg_b64: string;
  dims: [number, number];
  changed_tiles: Rect[];
}
export interface VadMsg {
  type: "vad";
  t: number;
  speaking: boolean;
  score: number;
}
export interface UtteranceMsg {
  type: "utterance";
  t_start: number;
  t_end: number;
  role: "user" | "agent";
  text: string;
  lang?: Lang | null;
  event_id: string;
}
export interface AgentStateMsg {
  type: "agent_state";
  t: number;
  mode: "speaking" | "listening";
}
export interface ControlMsg {
  type: "control";
  t: number;
  action: ControlAction;
}

// ---------- learner nudges (v2.1, docs/CONTRACTS.md "Learner nudges") ----------
export type NudgeKind = "predict" | "why" | "check" | "diverge" | "confirm_step";
export interface NudgeOption { id: string; label: string; /** experts-differ: whose way this is */ expert_id?: string | null }
export interface NudgeMsg {
  type: "nudge"; id: string; step_id?: string | null; kind: NudgeKind; question: string;
  options: NudgeOption[]; allow_dont_know: boolean;
  reference?: { keyframe_ids: string[]; quote?: { text: string; speaker: string; lang: string; translation?: string | null; audio_clip?: string | null } | null } | null;
  spoken?: string; lang?: string;
}
export interface NudgeResultMsg {
  type: "nudge_result"; id: string;
  /** noted = the learner's record doesn't settle it: expert's reasoning, no verdict, no mastery change */
  outcome: "correct" | "incorrect" | "dont_know" | "skipped" | "implicit_correct" | "implicit_incorrect" | "noted";
  feedback_spoken?: string; show_reference?: boolean;
}
export interface NudgeResponseMsg {
  type: "nudge_response"; id: string; via: "voice" | "click" | "key" | "close" | "implicit";
  choice_id?: string | null; dont_know?: boolean; text?: string; t: number;
}

export type ClientMsg =
  | HelloMsg | ClockSyncOut | ActivityMsg | KeyframeMsg | VadMsg
  | UtteranceMsg | AgentStateMsg | ControlMsg | NudgeResponseMsg;

// ---------- WebSocket: server → client ----------

export interface EventsMsg { type: "events"; items: ScreenEvent[] }
export interface LedgerMsg { type: "ledger"; open: number; saved_for_later: number; top?: Unknown | null }
/** v2.2 "How Claros decided": when it chose to speak and what it noticed */
export interface Why {
  when: string;
  what: string;
  signals?: { silence_ms?: number; screen_settled_ms?: number; typing?: boolean; boundary?: string };
  scope?: "company" | "personal_judgment";
}
export interface AskMsg { type: "ask"; unknown_id: string; text: string; why?: Why | null }
export interface LookedUpMsg {
  type: "looked_up"; unknown_id?: string; unknown_summary: string; answer: string;
  source: { kind: "app_docs" | "onet" | "general"; title: string; url?: string | null };
}
export interface SignalsMsg {
  type: "signals"; typing: boolean; speaking: boolean;
  screen: "changing" | "settled" | "away"; gate: "quiet" | "ready" | "asking";
}
export interface InterveneMsg {
  type: "intervene"; guardrail_id: string; text: string; moment: Moment;
  /** learner-language rule text + expert quote line ("Anna said: «…»"), original quote kept for display */
  rule?: string; quote?: string | null; lang?: string; trigger?: string;
  quote_original?: { id: string; speaker: string; lang: string; text: string; audio_clip?: string | null } | null;
  /** true = the one firmer reminder on save/submit of a still-violating record */
  escalated?: boolean;
}
export interface ContextUpdateMsg { type: "context_update"; text: string }
export interface ShowMomentMsg { type: "show_moment"; moment: Moment }
export interface HighlightStepMsg { type: "highlight_step"; step_id: string }
export interface MapUpdatedMsg { type: "map_updated"; workflow_id: string; version: number }
export interface StatusMsg { type: "status"; level: "info" | "warn" | "error"; text: string }
/** brain → client: session moved to a new phase (e.g. capture ended → debrief). Voice session stays alive. */
export interface PhaseMsg { type: "phase"; phase: Mode }
/** brain → client when the expert says it by voice */
export interface ServerControlMsg { type: "control"; action: ControlAction }
export interface ClockSyncIn { type: "clock_sync"; client_t: number; server_t: number }

export type SayKind = "ask" | "intervene" | "debrief" | "teachback" | "tutor" | "ack";
export interface SayMsg { type: "say"; id: string; text: string; kind: SayKind; step_id?: string | null; lang?: string; why?: Why | null }

export type ServerMsg =
  | SayMsg | EventsMsg | LedgerMsg | AskMsg | InterveneMsg | ContextUpdateMsg
  | ShowMomentMsg | HighlightStepMsg | MapUpdatedMsg | StatusMsg | ClockSyncIn | ServerControlMsg | PhaseMsg
  | NudgeMsg | NudgeResultMsg | LookedUpMsg | SignalsMsg | DebriefMsg;

/** The spoken debrief's progress, mirrored on screen (server-driven: it asks, records, moves on). */
export interface DebriefMsg {
  type: "debrief";
  phase: "questions" | "teach_back" | "done";
  asked: number;
  current: Unknown | null;
  needs_second_run?: boolean;
}

export type ServerMsgType = ServerMsg["type"];
export type ServerMsgOf<T extends ServerMsgType> = Extract<ServerMsg, { type: T }>;

// ---------- ElevenLabs client tools ----------

export interface ClientToolParams {
  highlight_step: { step_id: string };
  show_moment: { keyframe_ids: string[]; t: number };
  go_off_record: Record<string, never>;
  go_on_record: Record<string, never>;
  open_map: { workflow_id: string };
  request_expert: { workflow_hint: string };
}
export type ClientToolName = keyof ClientToolParams;

export const API_BASE: string =
  process.env.NEXT_PUBLIC_CLAROS_API ?? "http://localhost:8787";
export const WS_BASE: string = API_BASE.replace(/^http/, "ws");
