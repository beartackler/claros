// Realistic demo data for the Claros web UI (used when the API is unreachable).
// Mirrors the server's seeded fixtures (wf_ap_invoice, experts Anna Keller + Marco Ruiz, one conflict).
import workmap from "./mock.workmap.json";
import type {
  CaptureRequest,
  MasteryNode,
  ScreenEvent,
  Unknown,
  User,
  WorkMap,
} from "@/lib/contracts";

export const ANNA: User = { id: "u_anna", name: "Anna Keller", role: "expert" };
export const MARCO: User = { id: "u_marco", name: "Marco Ruiz", role: "expert" };
/** The expert persona this browser acts as (demo has no auth). */
export const EXPERT = ANNA;
export const LEA: User = { id: "u_lea", name: "Lea Martin", role: "learner" };
export const IVAN: User = { id: "u_ivan", name: "Ivan Petrov", role: "learner" };

/** Snapshot of GET /api/workflows/wf_ap_invoice after POST /api/knowledge/seed
 *  (data/fixtures/workmap_ap.json + workmap_ap_expert2.json merged), so mock == live. */
export const MOCK_MAP = workmap as unknown as WorkMap;

export type WorkflowSummary = {
  workflow_id: string;
  name: string;
  apps: string[];
  coverage: WorkMap["coverage"];
  experts: User[];
  onet_code?: string;
  updated_at: number;
};

const now = Date.now();
export const MOCK_WORKFLOWS: WorkflowSummary[] = [
  { workflow_id: MOCK_MAP.workflow_id, name: MOCK_MAP.name, apps: MOCK_MAP.apps, coverage: MOCK_MAP.coverage, experts: MOCK_MAP.experts, onet_code: MOCK_MAP.onet?.occupation_code, updated_at: now - 3_600_000 },
];

export const MOCK_REQUESTS: CaptureRequest[] = [
  {
    id: "req_payment_run",
    workflow_hint: "Prepare the weekly payment run",
    requested_by: LEA,
    moment: { session_id: "sess_learn_lea_1003", keyframe_ids: ["kf_l_payment_01"], t: 4_200, utterance_ids: [] },
    onet: { occupation_code: "43-3031.00", occupation_title: "Bookkeeping, Accounting, and Auditing Clerks", task: "Prepare payment vouchers and process payments.", dwas: [], technologies: ["ERPNext"], score: 0.74 },
    status: "open",
    created_at: now - 25 * 60_000,
  },
  {
    id: "req_credit_note",
    workflow_hint: "Credit note against a submitted invoice — which account?",
    requested_by: IVAN,
    moment: { session_id: "sess_learn_ivan_1002", keyframe_ids: ["kf_l_credit_02"], t: 9_800, utterance_ids: [] },
    onet: { occupation_code: "43-3031.00", occupation_title: "Bookkeeping, Accounting, and Auditing Clerks", dwas: [], technologies: ["ERPNext"], score: 0.68 },
    status: "open",
    created_at: now - 3 * 3_600_000,
  },
  {
    id: "req_fx_invoice",
    workflow_hint: "Invoice in USD from a US supplier",
    requested_by: LEA,
    moment: { session_id: "sess_learn_lea_0929", keyframe_ids: ["kf_l_fx_01"], t: 1_200, utterance_ids: [] },
    status: "accepted",
    created_at: now - 2 * 86_400_000,
  },
];

export const MOCK_EVENTS: ScreenEvent[] = [
  { id: "ev1", seq: 1, t: 3_100, kind: "open", app: "ERPNext", entity_type: "Purchase Invoice", entity_id: "ACC-PINV-2026-00471", source: "system", confidence: 0.94, summary: "Opened purchase invoice ACC-PINV-2026-00471 (Nordwind GmbH)" },
  { id: "ev2", seq: 2, t: 11_800, kind: "navigate", app: "ERPNext", entity_type: "Purchase Order", entity_id: "PUR-ORD-2026-00118", source: "system", confidence: 0.88, summary: "Jumped to linked purchase order PUR-ORD-2026-00118" },
  { id: "ev3", seq: 3, t: 22_400, kind: "edit", app: "ERPNext", entity_type: "Purchase Invoice", field: "Expense Account", canonical: "line.expense_account", old: "6300 Office", new: "0210 Capital Equipment", source: "typed", confidence: 0.86, summary: "Account changed to Capital Equipment on line 2 (5,200)" },
  { id: "ev4", seq: 4, t: 35_000, kind: "edit", app: "ERPNext", entity_type: "Purchase Invoice", field: "Cost Center", canonical: "line.cost_center", old: "Main", new: "Operations", source: "typed", confidence: 0.9, summary: "Cost center changed Main → Operations on line 2" },
  { id: "ev5", seq: 5, t: 47_600, kind: "hold", app: "ERPNext", entity_type: "Purchase Invoice", source: "system", confidence: 0.8, summary: "Invoice put on hold — reason: 'Nordwind December statement check'" },
];

export const MOCK_UNKNOWNS: Unknown[] = [
  { id: "u1", type: "limit", scope: "company", about_event_ids: ["ev3"], entity: "Expense Account", hypothesis: "Equipment over 5,000 is booked as capex", hypothesis_confidence: 0.72, spoken_question: "Equipment over 5,000 goes to capex here — is that your rule?", priority: 0.82, status: "asked", created_t: 22_900, answer_utterance_ids: [] },
  { id: "u2", type: "why", scope: "personal_judgment", about_event_ids: ["ev4"], entity: "Cost Center", hypothesis: "Equipment lines go to Operations", hypothesis_confidence: 0.5, spoken_question: "Why Operations instead of Main for this line?", priority: 0.6, status: "deferred", created_t: 35_400, answer_utterance_ids: [] },
  { id: "u3", type: "why", scope: "app", about_event_ids: ["ev2"], entity: "Purchase Order", priority: 0.3, status: "resolved", created_t: 12_000, answer_utterance_ids: [], resolution: "Linked PO shows ordered qty and requester.", resolution_source: "app_docs:https://docs.erpnext.com/docs/user/manual/en/purchase-order" },
  { id: "u4", type: "why", scope: "personal_judgment", about_event_ids: ["ev5"], entity: "Hold", spoken_question: "Why hold Nordwind invoices in December?", priority: 0.88, status: "deferred", created_t: 48_000, answer_utterance_ids: [] },
  { id: "u5", type: "why", scope: "universal", about_event_ids: ["ev3"], entity: "Capital Equipment", priority: 0.2, status: "resolved", created_t: 35_500, answer_utterance_ids: [], resolution: "Capex is capitalised and depreciated.", resolution_source: "llm" },
];

export const MOCK_MASTERY: MasteryNode[] = MOCK_MAP.steps.map((st, i) => ({
  step_id: st.id,
  level: (["unaided", "hinted", "caught", "unaided", "unseen", "hinted", "unaided"] as const)[i % 7],
  attempts: 1,
}));

/** Plausible wrong answers for learner prediction prompts (desirable difficulty). Keyed by step/guardrail id. */
export const MOCK_DISTRACTORS: Record<string, string[]> = {
  s2: ["Process it anyway and flag it later", "Create the supplier yourself from the invoice"],
  s3: ["Skip the receipt if the PO matches", "Reject any quantity difference"],
  s4: ["Book all equipment to office expense", "Capitalise anything over 500"],
  s5: ["Pay immediately to keep the discount", "Reject the invoice back to the supplier"],
  g1: ["The supplier asked for it", "Expense accounts are full at month end"],
  g2: ["December payments are processed by another team", "The bank is closed over the holidays"],
  g3: ["Subsidiaries use a different currency", "The system can't submit them"],
  g4: ["New suppliers get a discount", "The system needs a day to sync"],
  h2: ["New suppliers get a discount", "The system needs a day to sync"],
};
