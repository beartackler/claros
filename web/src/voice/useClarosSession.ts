"use client";
/**
 * Session plumbing: create a session (REST), open the WS, pipe server messages
 * into the zustand store. Voice + capture both use the shared socket via getSocket().
 *
 *   const { createSession, sessionId } = useClarosSession();
 *   await createSession({ mode: "capture", user, lang: "en" });
 */
import { useCallback, useEffect, useState } from "react";
import {
  API_BASE, type ControlAction, type CreateSessionRequest, type CreateSessionResponse, type HelloMsg,
} from "@/lib/contracts";
import { now } from "@/lib/clock";
import { connectSession, disconnectSession, getSocket, onSocketChange, type ClarosSocket } from "@/lib/ws";
import { useClaros } from "./store";

async function createSessionApi(req: CreateSessionRequest): Promise<string> {
  const r = await fetch(`${API_BASE}/api/sessions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) throw new Error(`POST /api/sessions ${r.status}`);
  const j = (await r.json()) as CreateSessionResponse;
  return j.session_id;
}

async function endSessionApi(sessionId: string): Promise<void> {
  await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/end`, { method: "POST" }).catch(() => {});
}

/** Wire a socket's server messages into the store. Returns unsubscribe. */
function wire(sock: ClarosSocket) {
  const { pushServer, set } = useClaros.getState();
  const offAny = sock.onAny((m) => {
    if (m.type === "clock_sync") return;
    pushServer(m);
  });
  const offStatus = sock.onStatus((s) => set({ wsStatus: s }));
  set({ wsStatus: sock.status, sessionId: sock.sessionId });
  return () => {
    offAny();
    offStatus();
  };
}

let wiredFor: ClarosSocket | null = null;
let unwire: (() => void) | null = null;
function ensureWired(sock: ClarosSocket | null) {
  if (sock === wiredFor) return;
  unwire?.();
  unwire = null;
  wiredFor = sock;
  if (sock) unwire = wire(sock);
}

/** Open (or reuse) the WS for an existing session id. */
export function openSession(hello: Omit<HelloMsg, "type">): ClarosSocket {
  const s = connectSession(hello);
  ensureWired(s);
  return s;
}

export function sendControl(action: ControlAction) {
  getSocket()?.send({ type: "control", t: now(), action });
}

/** Close the session client-side (server already knows, e.g. it sent control end_task). */
export async function endSessionLocal() {
  useClaros.getState().set({ sessionEnded: true });
  disconnectSession();
  useClaros.getState().set({ wsStatus: "closed" });
}

export function useClarosSession() {
  const [sessionId, setSessionId] = useState<string | null>(() => getSocket()?.sessionId ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ensureWired(getSocket());
    return onSocketChange((s) => {
      ensureWired(s);
      setSessionId(s?.sessionId ?? null);
    });
  }, []);

  const createSession = useCallback(async (req: CreateSessionRequest) => {
    setError(null);
    try {
      const id = await createSessionApi(req);
      useClaros.getState().reset();
      useClaros.getState().set({ phase: req.mode });
      openSession({ session_id: id, mode: req.mode, user: req.user, lang: req.lang, workflow_id: req.workflow_id ?? null });
      setSessionId(id);
      return id;
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  }, []);

  const join = useCallback((hello: Omit<HelloMsg, "type">) => {
    if (!useClaros.getState().phase) useClaros.getState().set({ phase: hello.mode });
    openSession(hello);
    setSessionId(hello.session_id);
  }, []);

  const end = useCallback(async () => {
    const id = getSocket()?.sessionId;
    sendControl("end_task");
    if (id) await endSessionApi(id);
    await endSessionLocal();
  }, []);

  return { sessionId, error, createSession, join, end, sendControl };
}
