import type { Guardrail, Quote, Step, Unknown, User, WorkMap } from "@/lib/contracts";

export const sortedSteps = (m: WorkMap) => [...m.steps].sort((a, b) => a.order - b.order);
export const quoteById = (m: WorkMap, id: string): Quote | undefined => m.quotes.find((q) => q.id === id);
export const quotesFor = (m: WorkMap, ids: string[] = []) => ids.map((id) => quoteById(m, id)).filter(Boolean) as Quote[];
/** A step's guardrails, with near-duplicates (same rule said twice by two experts) merged. */
export const guardrailsFor = (m: WorkMap, s: Step) => dedupeGuardrails(s.guardrail_ids.map((id) => m.guardrails.find((g) => g.id === id)).filter(Boolean) as Guardrail[]);

const STOP = new Set("a an the and or of to for in on at is are be it its this that do does don't dont not no any anything else before after first then with by from as if than".split(" "));
const tokens = (txt: string) => new Set(txt.toLowerCase().replace(/[^\p{L}\p{N}\s']/gu, " ").split(/\s+/).filter((w) => w.length > 1 && !STOP.has(w)));
/** overlap coefficient: share of the shorter rule's words found in the longer one */
export function similarity(a: string, b: string) {
  const A = tokens(a), B = tokens(b);
  if (!A.size || !B.size) return 0;
  let n = 0;
  A.forEach((w) => B.has(w) && n++);
  return n / Math.min(A.size, B.size);
}
export function dedupeGuardrails(gs: Guardrail[], threshold = 0.6): Guardrail[] {
  const out: Guardrail[] = [];
  for (const g of gs) {
    const i = out.findIndex((o) => o.action === g.action && similarity(o.text, g.text) >= threshold);
    if (i < 0) {
      out.push(g);
      continue;
    }
    const o = out[i];
    const keep = g.text.length > o.text.length ? g : o;
    out[i] = {
      ...keep,
      experts: [...new Set([...o.experts, ...g.experts])],
      quote_ids: [...new Set([...o.quote_ids, ...g.quote_ids])],
      evidence: [...o.evidence, ...g.evidence],
      approved: o.approved && g.approved,
    };
  }
  return out;
}

/** Short, scannable label for a rule (≤40 chars): the clause before ":" or a word-boundary cut. */
export function shortTitle(text: string, max = 40) {
  const head = text.split(/[:—–]/)[0].trim();
  const base = head.length >= 8 && head.length <= max ? head : text.trim();
  if (base.length <= max) return base.replace(/[.;,]$/, "");
  const cut = base.slice(0, max).replace(/\s+\S*$/, "");
  return `${cut}…`;
}
export const expertById = (m: WorkMap, id: string): User => m.experts.find((e) => e.id === id) ?? { id, name: id.replace(/^u_/, ""), role: "expert" };
export const firstName = (name: string) => name.split(/\s+/)[0];
export const isConfirmed = (s: Step) => s.approved && !s.conflict;
export const stepHighlight = (s: Step) =>
  s.decision?.from_value || s.decision?.to_value
    ? { label: s.title, from: s.decision?.from_value, to: s.decision?.to_value }
    : null;

export function shuffle<T>(arr: T[], seed: number): T[] {
  const a = [...arr];
  let x = seed || 1;
  for (let i = a.length - 1; i > 0; i--) {
    x = (x * 9301 + 49297) % 233280;
    const j = Math.floor((x / 233280) * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/* ---------- Work Map model: ordered groups (partial order) + typed items per step ---------- */

export type StepGroup = { depth: number; steps: Step[] };

/** Longest-path depth over `after` edges. Steps sharing a depth can happen in any order. */
export function stepGroups(m: WorkMap): StepGroup[] {
  const steps = sortedSteps(m);
  const byId = new Map(steps.map((s) => [s.id, s]));
  const depth = new Map<string, number>();
  const d = (s: Step, guard = 0): number => {
    if (depth.has(s.id)) return depth.get(s.id)!;
    const v = guard > 64 ? 0 : Math.max(-1, ...s.after.map((a) => (byId.get(a) ? d(byId.get(a)!, guard + 1) : -1))) + 1;
    depth.set(s.id, v);
    return v;
  };
  steps.forEach((s) => d(s));
  const groups: StepGroup[] = [];
  steps.forEach((s) => {
    const k = depth.get(s.id)!;
    const g = groups.find((x) => x.depth === k);
    if (g) g.steps.push(s);
    else groups.push({ depth: k, steps: [s] });
  });
  return groups.sort((a, b) => a.depth - b.depth);
}

/** Display order = group order, then step order inside a group. Mini-map and list both use this. */
export const flatOrder = (groups: StepGroup[]) => groups.flatMap((g) => g.steps);

export type MapFilter = "all" | "judgment" | "guardrail" | "conflict" | "unconfirmed";
export const isJudgment = (s: Step) => s.decision?.kind === "judgment";
export const hasGuardrail = (s: Step) => s.guardrail_ids.length > 0;
export const hasConflict = (s: Step) => Boolean(s.conflict);
export const isUnconfirmed = (m: WorkMap, s: Step) => !s.approved || guardrailsFor(m, s).some((g) => !g.approved);
export function matches(m: WorkMap, s: Step, f: MapFilter) {
  if (f === "judgment") return isJudgment(s);
  if (f === "guardrail") return hasGuardrail(s);
  if (f === "conflict") return hasConflict(s);
  if (f === "unconfirmed") return isUnconfirmed(m, s);
  return true;
}

/* ---------- honest coverage (derived from the map itself, not the server's ratios) ---------- */

export function honestCoverage(m: WorkMap) {
  const steps = m.steps;
  const conflicted = new Set(steps.filter((s) => s.conflict).map((s) => s.id));
  const evidence = steps.length ? steps.filter((s) => s.moment?.keyframe_ids?.length).length / steps.length : 0;
  const judg = steps.filter(isJudgment);
  const explained = judg.length ? judg.filter((s) => !s.conflict && s.decision?.reason_quote_ids.length).length / judg.length : 1;
  const rules = dedupeGuardrails(m.guardrails);
  const onConflict = (gid: string) => steps.some((s) => conflicted.has(s.id) && s.guardrail_ids.includes(gid));
  const rulesOk = rules.length ? rules.filter((g) => g.approved && !onConflict(g.id)).length / rules.length : 1;
  return { evidence, explained, rulesOk, open: openQuestions(m).length, conflicts: conflicted.size, unapproved: steps.filter((s) => !s.approved).length };
}

/* ---------- open questions: conflicts collapse to one neutral entry per step ---------- */

export type Position = { expert_id: string; description: string; reason_quote_ids: string[] };
export function positions(step: Step): Position[] {
  return [
    ...step.experts
      .filter((id) => !step.variants.some((v) => v.expert_id === id))
      .slice(0, step.decision ? 1 : 0)
      .map((id) => ({ expert_id: id, description: step.decision!.description, reason_quote_ids: step.decision!.reason_quote_ids })),
    ...step.variants,
  ];
}

export type OpenQuestion =
  | { kind: "conflict"; id: string; step: Step; positions: Position[]; unknowns: Unknown[] }
  | { kind: "unknown"; id: string; unknown: Unknown };

export function openQuestions(m: WorkMap): OpenQuestion[] {
  const out: OpenQuestion[] = [];
  const conflictUs = (m.open_unknowns ?? []).filter((u) => u.type === "conflict");
  for (const s of m.steps.filter((x) => x.conflict)) {
    const us = conflictUs.filter((u) => !u.about_event_ids.length || (u.spoken_question ?? "").includes(s.title));
    out.push({ kind: "conflict", id: `c_${s.id}`, step: s, positions: positions(s), unknowns: us.length ? us : conflictUs });
  }
  // a conflict unknown with no conflicted step still counts once
  if (!m.steps.some((x) => x.conflict) && conflictUs.length) out.push({ kind: "unknown", id: conflictUs[0].id, unknown: conflictUs[0] });
  for (const u of m.open_unknowns ?? []) if (u.type !== "conflict") out.push({ kind: "unknown", id: u.id, unknown: u });
  return out;
}
