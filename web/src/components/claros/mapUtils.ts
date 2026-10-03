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
