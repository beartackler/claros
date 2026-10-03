// Realistic demo data for the Claros web UI (used when the API is unreachable).
// Workflow: Accounts payable — post a vendor invoice in ERPNext. Two experts:
// Sabine Keller (speaks German) and Dmitri Orlov (speaks Russian).
import type {
  CaptureRequest,
  MasteryNode,
  ScreenEvent,
  Unknown,
  User,
  WorkMap,
} from "@/lib/contracts";

export const SABINE: User = { id: "u_sabine", name: "Sabine Keller", role: "expert" };
export const DMITRI: User = { id: "u_dmitri", name: "Dmitri Orlov", role: "expert" };
export const LEA: User = { id: "u_lea", name: "Lea Martin", role: "learner" };
export const IVAN: User = { id: "u_ivan", name: "Ivan Petrov", role: "learner" };

const S1 = "sess_cap_sabine_0927";
const S2 = "sess_cap_dmitri_0930";

const m = (session_id: string, kf: string, t: number) => ({
  session_id,
  keyframe_ids: [kf],
  t,
  utterance_ids: [],
});

export const MOCK_MAP: WorkMap = {
  id: "map_ap_invoice_v3",
  workflow_id: "wf_ap_purchase_invoice",
  version: 3,
  name: "Post a vendor invoice (accounts payable)",
  apps: ["ERPNext"],
  onet: {
    occupation_code: "43-3031.00",
    occupation_title: "Bookkeeping, Accounting, and Auditing Clerks",
    task_id: "6461",
    task: "Check figures, postings, and documents for correct entry, mathematical accuracy, and proper codes.",
    dwas: ["Verify accuracy of financial information.", "Code data or other information."],
    technologies: ["ERPNext", "SAP", "Microsoft Excel"],
    score: 0.86,
  },
  experts: [SABINE, DMITRI],
  session_ids: [S1, S2],
  steps: [
    {
      id: "st_open",
      order: 1,
      after: [],
      title: "Open the draft purchase invoice from the AP queue",
      state_signature: { app: "ERPNext", view: "Purchase Invoice list", entity_type: "Purchase Invoice" },
      moment: m(S1, "kf_s1_004", 12_400),
      decision: { kind: "routine", description: "Pick the oldest draft first", reason_quote_ids: ["q_oldest"] },
      guardrail_ids: [],
      context_note_ids: ["cn_pi_doc"],
      experts: [SABINE.id, DMITRI.id],
      variants: [],
      approved: true,
    },
    {
      id: "st_match",
      order: 2,
      after: ["st_open"],
      title: "Match supplier, PO and receipt (three-way match)",
      state_signature: { app: "ERPNext", view: "Purchase Invoice form", entity_type: "Purchase Invoice" },
      moment: m(S1, "kf_s1_019", 48_900),
      decision: {
        kind: "judgment",
        description: "Accept a quantity mismatch only if the receipt is partial and the PO stays open",
        reason_quote_ids: ["q_partial"],
        counterfactual: "If the PO is closed, the invoice goes on hold instead.",
      },
      guardrail_ids: [],
      context_note_ids: ["cn_3way"],
      experts: [SABINE.id, DMITRI.id],
      variants: [
        { expert_id: SABINE.id, description: "Checks the receipt before the PO", reason_quote_ids: [] },
        { expert_id: DMITRI.id, description: "Checks the PO first, then the receipt", reason_quote_ids: [] },
      ],
      approved: true,
    },
    {
      id: "st_costcenter",
      order: 3,
      after: ["st_match"],
      title: "Correct the cost center on the invoice lines",
      state_signature: { app: "ERPNext", view: "Purchase Invoice form", entity_type: "Purchase Invoice" },
      moment: m(S1, "kf_s1_031", 96_200),
      decision: {
        kind: "judgment",
        description: "Change cost center 4711 → 0400 for marketing services from Brandwerk GmbH",
        from_value: "4711",
        to_value: "0400",
        reason_quote_ids: ["q_costcenter"],
        counterfactual: "If the PO was raised by Sales, 4711 is correct.",
      },
      guardrail_ids: [],
      context_note_ids: ["cn_costcenter"],
      experts: [SABINE.id],
      variants: [],
      approved: true,
    },
    {
      id: "st_capex",
      order: 4,
      after: ["st_match"],
      title: "Classify equipment: capex or expense",
      state_signature: { app: "ERPNext", view: "Purchase Invoice form", entity_type: "Purchase Invoice" },
      moment: m(S1, "kf_s1_044", 141_700),
      decision: {
        kind: "judgment",
        description: "Equipment over 5,000 EUR net goes to the fixed-asset account, not expense",
        reason_quote_ids: ["q_capex"],
      },
      guardrail_ids: ["g_capex"],
      context_note_ids: ["cn_capex_universal"],
      experts: [SABINE.id, DMITRI.id],
      variants: [],
      approved: true,
    },
    {
      id: "st_bank",
      order: 5,
      after: ["st_costcenter", "st_capex"],
      title: "Compare supplier bank details with the master record",
      state_signature: { app: "ERPNext", view: "Supplier", entity_type: "Supplier" },
      moment: m(S2, "kf_s2_012", 64_300),
      decision: {
        kind: "judgment",
        description: "Any IBAN change on an invoice is a stop: call the supplier on the known number",
        reason_quote_ids: ["q_iban"],
      },
      guardrail_ids: ["g_iban"],
      context_note_ids: [],
      experts: [DMITRI.id, SABINE.id],
      variants: [],
      approved: true,
    },
    {
      id: "st_missingpo",
      order: 6,
      after: ["st_bank"],
      title: "Invoice without a PO reference",
      state_signature: { app: "ERPNext", view: "Purchase Invoice form", entity_type: "Purchase Invoice" },
      moment: m(S2, "kf_s2_027", 132_000),
      decision: {
        kind: "judgment",
        description: "Hold the invoice vs. post with a note",
        reason_quote_ids: ["q_po_hold", "q_po_note"],
      },
      guardrail_ids: [],
      context_note_ids: [],
      experts: [SABINE.id, DMITRI.id],
      variants: [
        { expert_id: SABINE.id, description: "Put on hold, email the requester for a PO", reason_quote_ids: ["q_po_hold"] },
        { expert_id: DMITRI.id, description: "Post if under 500 EUR and note the requester", reason_quote_ids: ["q_po_note"] },
      ],
      conflict: "Sabine always holds; Dmitri posts small amounts with a note.",
      approved: false,
    },
    {
      id: "st_submit",
      order: 7,
      after: ["st_missingpo"],
      title: "Submit, or route for approval above 10,000 EUR",
      state_signature: { app: "ERPNext", view: "Purchase Invoice form", entity_type: "Purchase Invoice" },
      moment: m(S1, "kf_s1_058", 201_300),
      decision: {
        kind: "judgment",
        description: "Above 10,000 EUR gross, assign to the Head of Finance before submitting",
        reason_quote_ids: ["q_approval"],
      },
      guardrail_ids: ["g_approval"],
      context_note_ids: ["cn_submit_doc"],
      experts: [SABINE.id],
      variants: [],
      approved: false,
    },
  ],
  guardrails: [
    {
      id: "g_capex",
      text: "Equipment over 5,000 EUR net must be booked to Fixed Assets (capex), never to expense.",
      quote_ids: ["q_capex"],
      predicate: { ">": [{ var: "line.net_amount" }, 5000] },
      fuzzy: false,
      action: "block_and_explain",
      owner: "Controlling",
      evidence: [m(S1, "kf_s1_044", 141_700)],
      experts: [SABINE.id, DMITRI.id],
      approved: true,
    },
    {
      id: "g_iban",
      text: "If the invoice IBAN differs from the supplier master, stop and verify by phone on the known number.",
      quote_ids: ["q_iban"],
      predicate: { "!=": [{ var: "invoice.iban" }, { var: "supplier.iban" }] },
      fuzzy: false,
      action: "stop_and_ask",
      owner: "Treasury",
      evidence: [m(S2, "kf_s2_012", 64_300)],
      experts: [DMITRI.id, SABINE.id],
      approved: true,
    },
    {
      id: "g_approval",
      text: "Invoices above 10,000 EUR gross need Head of Finance approval before submit.",
      quote_ids: ["q_approval"],
      predicate: { ">": [{ var: "invoice.grand_total" }, 10000] },
      fuzzy: false,
      action: "hold",
      owner: "Head of Finance",
      evidence: [m(S1, "kf_s1_058", 201_300)],
      experts: [SABINE.id],
      approved: false,
    },
  ],
  quotes: [
    { id: "q_oldest", speaker: "Sabine Keller", speaker_id: SABINE.id, lang: "de", text: "Immer die älteste zuerst, sonst verpassen wir Skonto.", translations: { en: "Always the oldest first, otherwise we miss the early-payment discount.", ru: "Всегда сначала самый старый, иначе теряем скидку за раннюю оплату." }, t: 12_000, session_id: S1, source: "live" },
    { id: "q_partial", speaker: "Sabine Keller", speaker_id: SABINE.id, lang: "de", text: "Teillieferung ist okay, solange die Bestellung offen bleibt.", translations: { en: "A partial delivery is fine as long as the order stays open.", ru: "Частичная поставка — нормально, пока заказ остаётся открытым." }, t: 50_100, session_id: S1, source: "live" },
    { id: "q_costcenter", speaker: "Sabine Keller", speaker_id: SABINE.id, lang: "de", text: "Brandwerk ist Marketing. Seit der Umstrukturierung läuft das über 0400, egal was auf der Bestellung steht.", translations: { en: "Brandwerk is marketing. Since the reorg it runs through 0400, no matter what the PO says.", ru: "Brandwerk — это маркетинг. После реорганизации всё идёт через 0400, что бы ни было в заказе." }, t: 97_000, session_id: S1, source: "live", audio_clip: "/api/clips/q_costcenter.mp3" },
    { id: "q_capex", speaker: "Sabine Keller", speaker_id: SABINE.id, lang: "de", text: "Über fünftausend netto ist das Anlagevermögen. Das prüft die Revision jedes Jahr.", translations: { en: "Over five thousand net is a fixed asset. The auditors check that every year.", ru: "Больше пяти тысяч нетто — это основные средства. Аудит проверяет это каждый год." }, t: 142_500, session_id: S1, source: "debrief", audio_clip: "/api/clips/q_capex.mp3" },
    { id: "q_iban", speaker: "Dmitri Orlov", speaker_id: DMITRI.id, lang: "ru", text: "Если IBAN другой — стоп. Звоним поставщику по старому номеру, не по номеру из письма.", translations: { en: "If the IBAN is different — stop. We call the supplier on the old number, not the one in the email.", de: "Wenn die IBAN anders ist — Stopp. Wir rufen den Lieferanten unter der alten Nummer an, nicht der aus der Mail." }, t: 65_000, session_id: S2, source: "live", audio_clip: "/api/clips/q_iban.mp3" },
    { id: "q_po_hold", speaker: "Sabine Keller", speaker_id: SABINE.id, lang: "de", text: "Ohne Bestellung geht nichts raus. Ich setze sie auf Halt und schreibe dem Anforderer.", translations: { en: "Nothing goes out without a PO. I put it on hold and write to the requester.", ru: "Без заказа ничего не проводим. Ставлю на удержание и пишу заказчику." }, t: 160_000, session_id: S1, source: "debrief" },
    { id: "q_po_note", speaker: "Dmitri Orlov", speaker_id: DMITRI.id, lang: "ru", text: "Если меньше пятисот — провожу и оставляю комментарий, иначе мы утонем в мелочи.", translations: { en: "If it's under five hundred I post it and leave a note, otherwise we drown in small stuff.", de: "Unter fünfhundert buche ich und hinterlasse eine Notiz, sonst ertrinken wir im Kleinkram." }, t: 133_000, session_id: S2, source: "live" },
    { id: "q_approval", speaker: "Sabine Keller", speaker_id: SABINE.id, lang: "de", text: "Ab zehntausend brutto geht es an Frau Brandt, vorher nicht buchen.", translations: { en: "From ten thousand gross it goes to Ms. Brandt; don't post before that.", ru: "От десяти тысяч брутто — к госпоже Брандт, до этого не проводить." }, t: 202_000, session_id: S1, source: "live" },
  ],
  context_notes: [
    { id: "cn_pi_doc", text: "A Purchase Invoice in ERPNext records a supplier bill; Draft invoices can be edited, Submitted ones create GL entries.", scope: "app", source: "https://docs.erpnext.com/docs/user/manual/en/purchase-invoice" },
    { id: "cn_3way", text: "Three-way match compares the purchase order, the goods receipt and the invoice before payment.", scope: "universal", source: "llm" },
    { id: "cn_costcenter", text: "Cost centers group income and expenses by department for internal reporting.", scope: "app", source: "https://docs.erpnext.com/docs/user/manual/en/cost-center" },
    { id: "cn_capex_universal", text: "Capital expenditure is capitalised and depreciated; operating expense hits the P&L immediately.", scope: "universal", source: "llm" },
    { id: "cn_submit_doc", text: "Checking postings for correct entry and proper codes is a core task of this occupation.", scope: "occupation", source: "onet:43-3031.00" },
  ],
  canonical_vars: { "line.cost_center": ["Cost Center", "Kostenstelle", "Центр затрат"] },
  open_unknowns: [
    {
      id: "u_po_conflict",
      type: "conflict",
      scope: "company",
      about_event_ids: [],
      entity: "Purchase Invoice",
      spoken_question: "Sabine holds invoices without a PO, Dmitri posts small ones. Which is the rule?",
      priority: 0.9,
      status: "open",
      created_t: 0,
      answer_utterance_ids: [],
    },
  ],
  exam: [
    { variant: "Laptop order 6,200 EUR net, PO matches", predicted: "Book to Fixed Assets 0210, block expense account", confidence: 0.93, expert_verdict: "correct" },
    { variant: "Brandwerk invoice, PO raised by Sales", predicted: "Keep cost center 4711", confidence: 0.71, expert_verdict: "correct" },
    { variant: "IBAN changed, supplier email says 'new bank'", predicted: "Stop, call supplier on master-record number", confidence: 0.95, expert_verdict: "correct" },
  ],
  coverage: {
    status: "partial",
    steps_with_evidence: 1,
    judgments_complete: 0.83,
    guardrails_complete: 0.67,
    open_unknowns: 1,
    conflicts: 1,
  },
  approved_by: [SABINE.id],
};

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
  { workflow_id: "wf_ap_purchase_invoice", name: MOCK_MAP.name, apps: ["ERPNext"], coverage: MOCK_MAP.coverage, experts: [SABINE, DMITRI], onet_code: "43-3031.00", updated_at: now - 3_600_000 },
  { workflow_id: "wf_ap_supplier_onboard", name: "Create a new supplier with bank details", apps: ["ERPNext"], coverage: { status: "ready", steps_with_evidence: 1, judgments_complete: 1, guardrails_complete: 1, open_unknowns: 0, conflicts: 0 }, experts: [DMITRI], onet_code: "43-3031.00", updated_at: now - 86_400_000 },
  { workflow_id: "wf_ap_payment_run", name: "Prepare the weekly payment run", apps: ["ERPNext"], coverage: { status: "missing", steps_with_evidence: 0, judgments_complete: 0, guardrails_complete: 0, open_unknowns: 0, conflicts: 0 }, experts: [], onet_code: "43-3031.00", updated_at: now - 172_800_000 },
  { workflow_id: "wf_ap_credit_note", name: "Book a supplier credit note", apps: ["ERPNext"], coverage: { status: "partial", steps_with_evidence: 0.6, judgments_complete: 0.5, guardrails_complete: 1, open_unknowns: 2, conflicts: 0 }, experts: [SABINE], onet_code: "43-3031.00", updated_at: now - 259_200_000 },
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
  { id: "ev1", seq: 1, t: 3_100, kind: "open", app: "ERPNext", entity_type: "Purchase Invoice", entity_id: "ACC-PINV-2026-00471", source: "system", confidence: 0.94, summary: "Opened purchase invoice ACC-PINV-2026-00471 (Brandwerk GmbH)" },
  { id: "ev2", seq: 2, t: 11_800, kind: "navigate", app: "ERPNext", entity_type: "Purchase Order", entity_id: "PUR-ORD-2026-00118", source: "system", confidence: 0.88, summary: "Jumped to linked purchase order PUR-ORD-2026-00118" },
  { id: "ev3", seq: 3, t: 22_400, kind: "edit", app: "ERPNext", entity_type: "Purchase Invoice", field: "Cost Center", canonical: "line.cost_center", old: "4711", new: "0400", source: "typed", confidence: 0.91, summary: "Cost center changed 4711 → 0400 on invoice 00471" },
  { id: "ev4", seq: 4, t: 35_000, kind: "edit", app: "ERPNext", entity_type: "Purchase Invoice", field: "Expense Account", canonical: "line.expense_account", old: "6300 Office", new: "0210 Fixed Assets", source: "typed", confidence: 0.86, summary: "Account changed to 0210 Fixed Assets on line 2 (6,200 EUR)" },
  { id: "ev5", seq: 5, t: 47_600, kind: "hold", app: "ERPNext", entity_type: "Purchase Invoice", source: "system", confidence: 0.8, summary: "Invoice put on hold — reason field: 'IBAN check'" },
];

export const MOCK_UNKNOWNS: Unknown[] = [
  { id: "u1", type: "why", scope: "personal_judgment", about_event_ids: ["ev3"], entity: "Cost Center", hypothesis: "Brandwerk invoices always go to marketing (0400)", hypothesis_confidence: 0.72, spoken_question: "Brandwerk always goes to 0400 now — is that your rule?", priority: 0.82, status: "asked", created_t: 22_900, answer_utterance_ids: [] },
  { id: "u2", type: "limit", scope: "company", about_event_ids: ["ev4"], entity: "Expense Account", hypothesis: "Equipment over 5,000 net is capex", hypothesis_confidence: 0.64, spoken_question: "Is 5,000 net the line for fixed assets here?", priority: 0.7, status: "deferred", created_t: 35_400, answer_utterance_ids: [] },
  { id: "u3", type: "why", scope: "app", about_event_ids: ["ev2"], entity: "Purchase Order", priority: 0.3, status: "resolved", created_t: 12_000, answer_utterance_ids: [], resolution: "Linked PO shows ordered qty and requester.", resolution_source: "app_docs:https://docs.erpnext.com/docs/user/manual/en/purchase-order" },
  { id: "u4", type: "stop_and_ask", scope: "company", about_event_ids: ["ev5"], entity: "IBAN", spoken_question: "Who do you call when the IBAN changed?", priority: 0.88, status: "deferred", created_t: 48_000, answer_utterance_ids: [] },
  { id: "u5", type: "why", scope: "universal", about_event_ids: ["ev4"], entity: "Fixed Assets", priority: 0.2, status: "resolved", created_t: 35_500, answer_utterance_ids: [], resolution: "Capex is capitalised and depreciated.", resolution_source: "llm" },
];

export const MOCK_MASTERY: MasteryNode[] = [
  { step_id: "st_open", level: "unaided", attempts: 1 },
  { step_id: "st_match", level: "hinted", attempts: 2 },
  { step_id: "st_costcenter", level: "caught", attempts: 1 },
  { step_id: "st_capex", level: "unaided", attempts: 1 },
  { step_id: "st_bank", level: "caught", attempts: 1 },
  { step_id: "st_missingpo", level: "unseen", attempts: 0 },
  { step_id: "st_submit", level: "hinted", attempts: 1 },
];

/** Plausible wrong answers for learner prediction prompts (desirable difficulty). */
export const MOCK_DISTRACTORS: Record<string, string[]> = {
  st_open: ["Pick the largest amount first", "Pick whichever supplier you know best"],
  st_match: ["Reject any quantity mismatch", "Ignore the receipt if the PO matches"],
  st_costcenter: ["Keep 4711 — it's what the PO says", "Ask the supplier which cost center to use"],
  st_capex: ["Book everything to office expense", "Capitalise anything over 500 EUR"],
  st_bank: ["Update the master record to the new IBAN", "Email the supplier at the address on the invoice"],
  st_missingpo: ["Post it and move on", "Reject it back to the supplier"],
  st_submit: ["Submit — approval happens after payment", "Split the invoice to stay under the limit"],
  g_capex: ["The supplier asked for it", "Expense accounts are full at month end"],
  g_iban: ["New IBANs take a day to activate", "The bank charges a fee for changes"],
  g_approval: ["Big invoices are paid by a different bank", "The system can't submit large amounts"],
};
