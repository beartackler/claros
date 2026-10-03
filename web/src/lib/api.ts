// Claros REST client with graceful fallback to demo data (lib/mock.ts).
// Every call returns { data, source } so surfaces can show a "demo data" chip.
import type {
  CaptureRequest,
  Coverage,
  MasteryNode,
  Moment,
  OnetMatch,
  ScreenState,
  User,
  WorkMap,
} from "@/lib/contracts";
import {
  MOCK_MAP,
  MOCK_MASTERY,
  MOCK_REQUESTS,
  MOCK_WORKFLOWS,
  type WorkflowSummary,
} from "@/lib/mock";

export const API_BASE = (process.env.NEXT_PUBLIC_CLAROS_API || "http://localhost:8787").replace(/\/$/, "");

export type Source = "live" | "mock";
export type Result<T> = { data: T; source: Source };

async function req<T>(path: string, init?: RequestInit, timeoutMs = 2500): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: { "content-type": "application/json", ...(init?.headers || {}) },
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

async function withFallback<T>(live: () => Promise<T>, mock: () => T): Promise<Result<T>> {
  try {
    return { data: await live(), source: "live" };
  } catch {
    return { data: mock(), source: "mock" };
  }
}

export const keyframeUrl = (id: string) => `${API_BASE}/api/keyframes/${encodeURIComponent(id)}.jpg`;
export const clipUrl = (path: string) => (path.startsWith("http") ? path : `${API_BASE}${path}`);
export const exportUrls = (workflowId: string) => ({
  skill: `${API_BASE}/api/export/${encodeURIComponent(workflowId)}.skill.md`,
  json: `${API_BASE}/api/workflows/${encodeURIComponent(workflowId)}`,
  mcp: `${API_BASE}/mcp`,
});

function toSummary(w: Partial<WorkMap> & Partial<WorkflowSummary>): WorkflowSummary {
  return {
    workflow_id: w.workflow_id || w.id || "unknown",
    name: w.name || "Untitled workflow",
    apps: w.apps || [],
    coverage: w.coverage || { status: "missing", steps_with_evidence: 0, judgments_complete: 0, guardrails_complete: 0, open_unknowns: 0, conflicts: 0 },
    experts: w.experts || [],
    onet_code: w.onet?.occupation_code || w.onet_code,
    updated_at: w.updated_at || Date.now(),
  };
}

export function listWorkflows(): Promise<Result<WorkflowSummary[]>> {
  return withFallback(
    async () => {
      const raw = await req<unknown>("/api/workflows");
      const arr = Array.isArray(raw) ? raw : ((raw as { items?: unknown[] })?.items ?? []);
      return (arr as Partial<WorkMap>[]).map(toSummary);
    },
    () => MOCK_WORKFLOWS,
  );
}

export function getWorkflow(id: string): Promise<Result<WorkMap>> {
  return withFallback(
    () => req<WorkMap>(`/api/workflows/${encodeURIComponent(id)}`),
    () => ({ ...MOCK_MAP, workflow_id: id === MOCK_MAP.workflow_id ? id : MOCK_MAP.workflow_id }),
  );
}

export function getCoverage(id: string): Promise<Result<Coverage>> {
  return withFallback(
    () => req<Coverage>(`/api/workflows/${encodeURIComponent(id)}/coverage`),
    () => MOCK_WORKFLOWS.find((w) => w.workflow_id === id)?.coverage ?? MOCK_MAP.coverage,
  );
}

export type LookupResult = {
  match?: { workflow_id: string; score: number; coverage: Coverage } | null;
  onet?: OnetMatch | null;
};

export function lookupWorkflow(body: {
  screen_state?: ScreenState | null;
  utterance: string;
  lang: string;
}, mockStatus?: Coverage["status"]): Promise<Result<LookupResult>> {
  return withFallback(
    () => req<LookupResult>("/api/workflows/lookup", { method: "POST", body: JSON.stringify(body) }, 6000),
    () => {
      const status = mockStatus ?? "partial";
      if (status === "missing") return { match: null, onet: MOCK_REQUESTS[0].onet };
      const wf = status === "ready" ? MOCK_WORKFLOWS[1] : MOCK_WORKFLOWS[0];
      return {
        match: { workflow_id: MOCK_MAP.workflow_id, score: 0.82, coverage: { ...wf.coverage, status } },
        onet: MOCK_MAP.onet,
      };
    },
  );
}

export function listRequests(): Promise<Result<CaptureRequest[]>> {
  return withFallback(
    async () => {
      const raw = await req<unknown>("/api/requests");
      return (Array.isArray(raw) ? raw : ((raw as { items?: unknown[] })?.items ?? [])) as CaptureRequest[];
    },
    () => MOCK_REQUESTS,
  );
}

export function createRequest(body: {
  workflow_hint: string;
  requested_by: User;
  moment?: Moment | null;
}): Promise<Result<CaptureRequest>> {
  return withFallback(
    () => req<CaptureRequest>("/api/requests", { method: "POST", body: JSON.stringify(body) }),
    () => ({
      id: `req_local_${Date.now().toString(36)}`,
      workflow_hint: body.workflow_hint,
      requested_by: body.requested_by,
      moment: body.moment ?? undefined,
      status: "open",
      created_at: Date.now(),
    } as CaptureRequest),
  );
}

export function acceptRequest(id: string): Promise<Result<{ session_id: string }>> {
  return withFallback(
    async () => {
      const r = await req<{ session_id?: string; id?: string }>(`/api/requests/${encodeURIComponent(id)}/accept`, { method: "POST" });
      return { session_id: r.session_id || r.id || `sess_${id}` };
    },
    () => ({ session_id: `sess_cap_${id}` }),
  );
}

export function createSession(body: {
  mode: "capture" | "debrief" | "learn" | "request";
  user: User;
  lang: string;
  workflow_id?: string;
}): Promise<Result<{ session_id: string }>> {
  return withFallback(
    () => req<{ session_id: string }>("/api/sessions", { method: "POST", body: JSON.stringify(body) }),
    () => ({ session_id: `sess_${body.mode}_${Date.now().toString(36)}` }),
  );
}

export function endSession(id: string): Promise<Result<unknown>> {
  return withFallback(
    () => req<unknown>(`/api/sessions/${encodeURIComponent(id)}/end`, { method: "POST" }),
    () => ({ ok: true }),
  );
}

export function getMastery(learnerId: string, workflowId: string): Promise<Result<MasteryNode[]>> {
  return withFallback(
    async () => {
      const raw = await req<unknown>(`/api/learners/${encodeURIComponent(learnerId)}/mastery?workflow_id=${encodeURIComponent(workflowId)}`);
      return (Array.isArray(raw) ? raw : ((raw as { nodes?: unknown[] })?.nodes ?? [])) as MasteryNode[];
    },
    () => MOCK_MASTERY,
  );
}

export const wsUrl = (sessionId: string) =>
  `${API_BASE.replace(/^http/, "ws")}/ws/session/${encodeURIComponent(sessionId)}`;
