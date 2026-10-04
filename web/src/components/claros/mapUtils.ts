import type { Guardrail, Quote, Step, User, WorkMap } from "@/lib/contracts";

export const sortedSteps = (m: WorkMap) => [...m.steps].sort((a, b) => a.order - b.order);
export const quoteById = (m: WorkMap, id: string): Quote | undefined => m.quotes.find((q) => q.id === id);
export const quotesFor = (m: WorkMap, ids: string[] = []) => ids.map((id) => quoteById(m, id)).filter(Boolean) as Quote[];
export const guardrailsFor = (m: WorkMap, s: Step) => s.guardrail_ids.map((id) => m.guardrails.find((g) => g.id === id)).filter(Boolean) as Guardrail[];
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
