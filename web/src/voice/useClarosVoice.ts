"use client";
/**
 * useClarosVoice — ElevenLabs agent session bound to a Claros session.
 *
 * - token: GET {api}/api/el/token?agent=claros  (server holds the API key)
 * - customLlmExtraBody {session_id, mode} + dynamicVariables {session_id, mode, lang, user_name}
 * - forwards final transcripts → `utterance`, VAD crossings → `vad`, mode → `agent_state`
 * - server `say {id,text,kind,step_id?}` (and legacy `ask` / `intervene`, mapped to say) → queued, sent as
 *   sendUserMessage("⟦TEXT⟧") only when the agent is listening and the user is quiet; the hosted LLM
 *   speaks TEXT verbatim. Segments with step_id highlight that step when sent (deduped vs. the agent's own
 *   highlight_step call). `context_update` → sendContextualUpdate
 * - dynamic variables come from GET {api}/api/dialog/vars?session_id= (mode, user_name, workflow_name, lang, workflow_brief)
 * - sendUserActivity() ≤1/s while the capture worker reports typing/scrolling
 * - client tools → zustand store (useClaros)
 * Must be rendered under <ConversationProvider> (see voice/providers.tsx).
 */
import { useCallback, useEffect, useRef } from "react";
import { useConversation } from "@elevenlabs/react";
import { API_BASE, type ClientToolParams, type Mode, type SayKind, type SayMsg } from "@/lib/contracts";
import { now } from "@/lib/clock";
import { getSocket, onSocketChange } from "@/lib/ws";
import { useClaros } from "./store";
import { endSessionLocal, sendControl } from "./useClarosSession";

export interface ClarosVoiceOptions {
  sessionId: string | null;
  mode: Mode;
  lang: string;
  userName?: string;
  /** spoken name of the workflow for the agent prompt */
  workflowName?: string;
  agent?: string; // agent alias for the token endpoint (default "claros")
}

const HIDDEN = /⟦[^⟧]*⟧/;
const SAY_QUIET_MS = 450;
const SAY_MAX_WAIT_MS = 12_000;
const HIGHLIGHT_DEDUPE_MS = 8_000;

export type { SayKind, SayMsg } from "@/lib/contracts";

type DialogVars = Record<string, string>;

async function fetchDialogVars(sessionId: string): Promise<DialogVars> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 3500);
  try {
    const r = await fetch(`${API_BASE}/api/dialog/vars?session_id=${encodeURIComponent(sessionId)}`, { signal: ctl.signal });
    if (!r.ok) return {};
    const j = (await r.json()) as Record<string, unknown>;
    const out: DialogVars = {};
    for (const [k, v] of Object.entries(j)) if (typeof v === "string") out[k] = v;
    return out;
  } catch {
    return {};
  } finally {
    clearTimeout(timer);
  }
}

const askKind = (id: string): SayKind =>
  id.startsWith("pred-") || id.startsWith("ex-") || id.startsWith("hint-") ? "tutor" : id.startsWith("phase-") ? "ack" : "ask";
const markerText = (t: string) => t.replace(/[⟦⟧]/g, "").replace(/\s+/g, " ").trim();
const VAD_ON = 0.6;
const VAD_OFF = 0.35;

type TokenResp = { token?: string; conversation_token?: string; signed_url?: string; agent_id?: string };

async function fetchToken(agent: string): Promise<TokenResp> {
  const r = await fetch(`${API_BASE}/api/el/token?agent=${encodeURIComponent(agent)}`);
  if (!r.ok) throw new Error(`token ${r.status}`);
  const ct = r.headers.get("content-type") ?? "";
  if (ct.includes("json")) return (await r.json()) as TokenResp;
  return { token: (await r.text()).trim() };
}

// A conversation token fetched while the window picker is open, so pressing Start doesn't wait on it.
// Tokens are short-lived, so a prefetched one is used only within 60 s.
let prefetched: { agent: string; at: number; p: Promise<TokenResp> } | null = null;
export function prefetchVoiceToken(agent = "claros") {
  const p = fetchToken(agent);
  p.catch(() => {});
  prefetched = { agent, at: Date.now(), p };
}
function takeToken(agent: string): Promise<TokenResp> {
  const pf = prefetched;
  prefetched = null;
  return pf && pf.agent === agent && Date.now() - pf.at < 60_000 ? pf.p.catch(() => fetchToken(agent)) : fetchToken(agent);
}

/** Start-up timing in the console, so a slow start shows which step stalled. */
export function voiceMark(step: string) {
  const w = window as unknown as { __clarosT0?: number };
  if (step === "start" || w.__clarosT0 === undefined) w.__clarosT0 = performance.now();
  console.info(`[claros voice] ${step} +${Math.round(performance.now() - w.__clarosT0)}ms`);
}

export function useClarosVoice({ sessionId, mode, lang, userName = "", workflowName = "this task", agent = "claros" }: ClarosVoiceOptions) {
  const set = useClaros((s) => s.set);
  const offRecord = useClaros((s) => s.offRecord);
  const modeRef = useRef(mode);
  useEffect(() => { modeRef.current = mode; }, [mode]);
  const vadSpeaking = useRef(false);
  const vadStart = useRef(0);
  const agentSpeakStart = useRef(0);
  const lastUserActivity = useRef(0);
  const statusRef = useRef<string>("disconnected");
  const agentModeRef = useRef<"speaking" | "listening">("listening");
  const sayQueue = useRef<SayMsg[]>([]);
  const sayInFlight = useRef<{ id: string; t: number; spoke: boolean } | null>(null);
  const sayTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastQuietT = useRef(0);
  const lastSayHighlight = useRef<{ step: string; t: number } | null>(null);
  const pumpRef = useRef<() => void>(() => {});

  const conv = useConversation({
    onStatusChange: ({ status }) => {
      statusRef.current = status;
      if (status === "connected") {
        voiceMark("connected");
        setTimeout(() => pumpRef.current(), SAY_QUIET_MS);
      }
      set({ voiceStatus: status === "connected" ? "connected" : status === "connecting" ? "connecting" : "disconnected" });
    },
    onError: (message) => {
      set({ voiceStatus: "error" });
      useClaros.getState().pushServer({ type: "status", level: "error", text: `voice: ${message}` });
    },
    onModeChange: ({ mode: m }) => {
      const t = now();
      if (m === "speaking") agentSpeakStart.current = t;
      agentModeRef.current = m;
      if (m === "speaking" && sayInFlight.current) sayInFlight.current.spoke = true;
      if (m === "listening") {
        lastQuietT.current = Date.now();
        if (sayInFlight.current?.spoke) sayInFlight.current = null;
        setTimeout(() => pumpRef.current(), SAY_QUIET_MS);
      }
      set({ agentMode: m });
      getSocket()?.send({ type: "agent_state", t, mode: m });
    },
    onVadScore: ({ vadScore }) => {
      const t = now();
      const was = vadSpeaking.current;
      const is = was ? vadScore > VAD_OFF : vadScore > VAD_ON;
      if (is !== was) {
        vadSpeaking.current = is;
        if (is) vadStart.current = t;
        else {
          lastQuietT.current = Date.now();
          setTimeout(() => pumpRef.current(), SAY_QUIET_MS);
        }
        set({ userSpeaking: is });
        getSocket()?.send({ type: "vad", t, speaking: is, score: vadScore });
      }
    },
    onMessage: ({ message, role, event_id }) => {
      if (!message || HIDDEN.test(message)) return; // never surface control tokens
      const t = now();
      const t_start =
        role === "user"
          ? vadStart.current && t - vadStart.current < 60_000 ? vadStart.current : t - 1500
          : agentSpeakStart.current && t - agentSpeakStart.current < 60_000 ? agentSpeakStart.current : t;
      const id = `${role}-${event_id}`;
      useClaros.getState().pushTranscript({ id, role, text: message, t: t_start });
      if (role === "agent") set({ caption: message });
      getSocket()?.send({ type: "utterance", t_start, t_end: t, role, text: message, lang, event_id: id });
    },
  });

  const convRef = useRef(conv);
  convRef.current = conv;
  const connected = () => statusRef.current === "connected";
  // The call is app-wide (ConversationProvider) but these callbacks only fire on CHANGES: a page that mounts
  // mid-call (capture → debrief) must read the live status, or it thinks it's disconnected and never speaks
  // its queue (live: every debrief question stayed on screen only).
  const liveStatus = conv.status;
  const liveSpeaking = conv.isSpeaking;
  useEffect(() => {
    const was = statusRef.current;
    statusRef.current = liveStatus;
    agentModeRef.current = liveSpeaking ? "speaking" : "listening";
    if (liveStatus === "connected" && was !== "connected") setTimeout(() => pumpRef.current(), SAY_QUIET_MS);
  }, [liveStatus, liveSpeaking]);

  // ---- off the record (honest): OFF by voice or tap; BACK ON only by tapping Resume ----
  // Off: Claros says so, mic muted, frames stop (capture checks the store). The muted mic can't hear
  // "back on the record", so the only way back is the Resume button (page, PiP orb, or R key).
  const enqueueRef = useRef<(m: SayMsg) => void>(() => {});
  const announceOff = useCallback(() => {
    sayQueue.current = sayQueue.current.filter((m) => m.id.startsWith("offrec-"));
    enqueueRef.current({ type: "say", id: `offrec-off-${Date.now()}`, text: offRecordLine(lang, "off"), kind: "ack" });
  }, [lang]);
  const setOffRecord = useCallback(
    (on: boolean) => {
      if (useClaros.getState().offRecord === on) return;
      set({ offRecord: on });
      try { convRef.current.setMuted(on); } catch {}
      sendControl(on ? "off_record_on" : "off_record_off");
      if (on) announceOff();
      else {
        // back on: drop any queued "off the record" / "still off the record" line before it's spoken late
        sayQueue.current = sayQueue.current.filter((m) => !m.id.startsWith("offrec-off-") && !m.id.startsWith("offrec-still-"));
        enqueueRef.current({ type: "say", id: `offrec-on-${Date.now()}`, text: offRecordLine(lang, "on"), kind: "ack" });
      }
    },
    [set, announceOff, lang],
  );
  /** "Strike that": the server deletes the last 30 s. */
  const strikeThat = useCallback(() => sendControl("strike_that"), []);
  // one spoken reminder after 3 min off the record, then at most every 5 min
  useEffect(() => {
    if (!offRecord) return;
    let iv: ReturnType<typeof setInterval> | null = null;
    const remind = () => enqueueRef.current({ type: "say", id: `offrec-still-${Date.now()}`, text: offRecordLine(lang, "still"), kind: "ack" });
    const first = setTimeout(() => {
      remind();
      iv = setInterval(remind, 5 * 60_000);
    }, 3 * 60_000);
    return () => {
      clearTimeout(first);
      if (iv) clearInterval(iv);
    };
  }, [offRecord, lang]);
  useEffect(() => {
    if (connected()) {
      try { convRef.current.setMuted(offRecord); } catch {}
    }
  }, [offRecord]);

  // ---- server → agent: say queue (never talk over the user or the agent) ----
  const pump = useCallback(() => {
    if (sayTimer.current) { clearTimeout(sayTimer.current); sayTimer.current = null; }
    if (!connected() || !sayQueue.current.length) return;
    const now_ = Date.now();
    const f = sayInFlight.current;
    if (f && now_ - f.t < SAY_MAX_WAIT_MS) { sayTimer.current = setTimeout(pump, 500); return; }
    if (agentModeRef.current === "speaking" || vadSpeaking.current || now_ - lastQuietT.current < SAY_QUIET_MS) {
      sayTimer.current = setTimeout(pump, 300);
      return;
    }
    // off the record: only the off-record notices may be spoken
    if (useClaros.getState().offRecord) {
      sayQueue.current = sayQueue.current.filter((x) => x.id.startsWith("offrec-"));
      if (!sayQueue.current.length) return;
    }
    const m = sayQueue.current.shift()!;
    sayInFlight.current = { id: m.id, t: now_, spoke: false };
    if (m.step_id) {
      lastSayHighlight.current = { step: m.step_id, t: now_ };
      useClaros.getState().pushTool("highlight_step", { step_id: m.step_id });
    }
    set({ caption: m.text });
    // no ID in the marker: if the agent echoes it, TTS can't read out a code
    try { convRef.current.sendUserMessage(`⟦${markerText(m.text)}⟧`); } catch { sayInFlight.current = null; }
  }, [set]);
  pumpRef.current = pump;
  const enqueueSay = useCallback((m: SayMsg) => {
    if (!m.text?.trim()) return;
    if (sayQueue.current.some((x) => x.id === m.id)) return;
    sayQueue.current.push(m);
    if (sayQueue.current.length > 20) sayQueue.current.splice(0, sayQueue.current.length - 20);
    pump();
  }, [pump]);

  useEffect(() => {
    enqueueRef.current = enqueueSay;
  }, [enqueueSay]);

  const dropSay = useCallback((pred: (m: SayMsg) => boolean) => {
    sayQueue.current = sayQueue.current.filter((m) => !pred(m));
  }, []);

  // ---- server → agent ----
  useEffect(() => {
    let offs: (() => void)[] = [];
    const attach = () => {
      offs.forEach((f) => f());
      offs = [];
      const s = getSocket();
      if (!s) return;
      offs.push(
        s.on("ask", (m) => enqueueSay({ type: "say", id: m.unknown_id, text: m.text, kind: askKind(m.unknown_id) })),
        s.on("intervene", (m) => {
          dropSay((x) => x.id.startsWith("nudge-")); // a hard stop makes any queued nudge stale
          enqueueSay({ type: "say", id: m.guardrail_id, text: m.text, kind: "intervene" });
        }),
        // live nudge card: spoken through the same queue (never over the user); answered before it was said → dropped
        s.on("nudge", (m) => enqueueSay({ type: "say", id: m.id, text: m.spoken || m.question, kind: "tutor", ...(m.step_id ? { step_id: m.step_id } : {}) })),
        s.on("nudge_result", (m) => dropSay((x) => x.id === m.id)),
        s.onAny((raw) => {
          const m = raw as unknown as SayMsg;
          if (m.type === "say" && m.id && typeof m.text === "string") enqueueSay(m);
        }),
        // brain-originated control (expert said it by voice): store already mirrors it; don't echo back
        s.on("control", (m) => {
          // said by voice → brain → control: mute + say so. "off_record_off" by voice can't happen (mic is muted).
          if (m.action === "off_record_on") {
            try { convRef.current.setMuted(true); } catch {}
            announceOff();
          }
          if (m.action === "off_record_off") {
            try { convRef.current.setMuted(false); } catch {}
          }
          if (m.action === "end_task") {
            try { convRef.current.endSession(); } catch {}
            void endSessionLocal();
          }
        }),
        s.on("context_update", (m) => { if (connected()) convRef.current.sendContextualUpdate(m.text); }),
      );
    };
    attach();
    const offChange = onSocketChange(attach);
    return () => {
      offChange();
      offs.forEach((f) => f());
    };
  }, [enqueueSay, dropSay, announceOff]);

  // ---- user is working → keep the agent from barging in ----
  useEffect(
    () =>
      useClaros.subscribe((s) => {
        if (!connected()) return;
        if (s.activity !== "typing" && s.activity !== "scrolling") return;
        const t = Date.now();
        if (t - lastUserActivity.current < 1000) return;
        lastUserActivity.current = t;
        try { convRef.current.sendUserActivity(); } catch {}
      }),
    [],
  );

  // ---- client tools ----
  const clientTools = useRef({
    highlight_step: (p: ClientToolParams["highlight_step"]) => {
      const last = lastSayHighlight.current;
      if (last && last.step === p.step_id && Date.now() - last.t < HIGHLIGHT_DEDUPE_MS) return "ok"; // already shown
      useClaros.getState().pushTool("highlight_step", p);
      return "ok";
    },
    show_moment: (p: ClientToolParams["show_moment"]) => {
      useClaros.getState().pushTool("show_moment", p);
      return "ok";
    },
    go_off_record: () => {
      useClaros.getState().pushTool("go_off_record", {});
      setOffRecordRef.current(true);
      return "off record";
    },
    // never resume by voice: the person must tap Resume (honest off-the-record)
    // the agent must not improvise around this (live: it read "Back on the record" as a command and invented
    // "I'm still off the record, tap Resume"); the app already announces both states itself
    go_on_record: () => "Nothing to do. Say nothing.",
    open_map: (p: ClientToolParams["open_map"]) => {
      useClaros.getState().pushTool("open_map", p);
      return "ok";
    },
    request_expert: (p: ClientToolParams["request_expert"]) => {
      // only a learner asks an expert; while an expert records, the expert IS the source
      if (modeRef.current !== "learn") return "Not available now. Say nothing.";
      useClaros.getState().pushTool("request_expert", p);
      return "request created";
    },
  });
  const setOffRecordRef = useRef(setOffRecord);
  setOffRecordRef.current = setOffRecord;

  const start = useCallback(async () => {
    if (!sessionId) throw new Error("no session");
    set({ voiceStatus: "connecting" });
    // no mic pre-check here: the start sequence already asked for permission, and the call opens the mic itself.
    // Every extra getUserMedia costs seconds on slow devices (Bluetooth headsets, a recorder holding the mic).
    // Session variables and the voice token are fetched in parallel.
    voiceMark("voice.start");
    const tokP = takeToken(agent).then((t) => ({ t }), (e: unknown) => ({ e }));
    const dv = await fetchDialogVars(sessionId);
    voiceMark("vars");
    const vars: Record<string, string> = {
      session_id: sessionId, mode, lang, user_name: userName || "there", workflow_name: workflowName, workflow_brief: "none yet",
      ...Object.fromEntries(Object.entries(dv).filter(([k]) => k !== "dialog_mode" && k !== "session_id" && k !== "mode")),
    };
    if (userName) vars.user_name = userName;
    const shortLang = (lang || "en").slice(0, 2);
    const common = {
      customLlmExtraBody: { session_id: sessionId, mode },
      dynamicVariables: vars,
      ...(["de", "fr", "es", "ru"].includes(shortLang) ? { overrides: { agent: { language: shortLang as "de" | "fr" | "es" | "ru" } } } : {}),
      clientTools: clientTools.current as unknown as Record<string, (p: unknown) => string>,
    };
    let tok: TokenResp = {};
    const tr_ = await tokP;
    voiceMark("token");
    try {
      if ("e" in tr_) throw tr_.e;
      tok = tr_.t;
    } catch (e) {
      const fallbackAgent = process.env.NEXT_PUBLIC_ELEVENLABS_AGENT_ID;
      if (!fallbackAgent) {
        set({ voiceStatus: "error" });
        useClaros.getState().pushServer({ type: "status", level: "error", text: `voice token: ${(e as Error).message}` });
        return;
      }
      tok = { agent_id: fallbackAgent };
    }
    const conversationToken = tok.token ?? tok.conversation_token;
    if (conversationToken) conv.startSession({ ...common, conversationToken, connectionType: "webrtc" });
    else if (tok.signed_url) conv.startSession({ ...common, signedUrl: tok.signed_url, connectionType: "websocket" });
    else if (tok.agent_id) conv.startSession({ ...common, agentId: tok.agent_id });
    else {
      set({ voiceStatus: "error" });
      useClaros.getState().pushServer({ type: "status", level: "error", text: "voice token: empty response" });
    }
  }, [sessionId, mode, lang, userName, workflowName, agent, conv, set]);

  const end = useCallback(() => {
    try { conv.endSession(); } catch {}
  }, [conv]);

  /** 0..1 output level from real agent audio (for the orb). */
  const getOutputLevel = useCallback(() => {
    if (!connected()) return 0;
    try {
      const d = convRef.current.getOutputByteFrequencyData();
      if (!d?.length) return convRef.current.getOutputVolume?.() ?? 0;
      let acc = 0;
      for (let i = 0; i < d.length; i++) acc += d[i];
      return Math.min(1, acc / d.length / 128);
    } catch {
      return 0;
    }
  }, []);
  const getInputLevel = useCallback(() => {
    if (!connected()) return 0;
    try { return Math.min(1, convRef.current.getInputVolume()); } catch { return 0; }
  }, []);

  return {
    status: conv.status,
    mode: conv.mode,
    isSpeaking: conv.isSpeaking,
    isMuted: conv.isMuted,
    offRecord,
    setOffRecord,
    strikeThat,
    start,
    end,
    sendText: (text: string) => conv.sendUserMessage(text),
    sendContextualUpdate: (text: string) => conv.sendContextualUpdate(text),
    getOutputByteFrequencyData: () => (connected() ? convRef.current.getOutputByteFrequencyData() : new Uint8Array()),
    getOutputLevel,
    getInputLevel,
  };
}

export type ClarosVoice = ReturnType<typeof useClarosVoice>;

/** What Claros says around off the record (localized; spoken verbatim through the say queue). */
const OFF_LINES: Record<string, Record<"off" | "on" | "still", string>> = {
  en: { off: "Off the record — I'm not watching or listening. Tap Resume when you're ready.", on: "Back on the record.", still: "Still off the record — tap Resume when you're ready." },
  ru: { off: "Не для записи — я не смотрю и не слушаю. Нажмите «Продолжить», когда будете готовы.", on: "Снова записываю.", still: "Всё ещё не для записи — нажмите «Продолжить», когда будете готовы." },
  de: { off: "Nicht für die Aufzeichnung — ich schaue und höre nicht zu. Tippe auf Fortsetzen, wenn du so weit bist.", on: "Wieder auf Aufnahme.", still: "Immer noch nicht für die Aufzeichnung — tippe auf Fortsetzen, wenn du so weit bist." },
  fr: { off: "Hors enregistrement — je ne regarde ni n'écoute. Touchez Reprendre quand vous êtes prêt.", on: "On reprend l'enregistrement.", still: "Toujours hors enregistrement — touchez Reprendre quand vous êtes prêt." },
  es: { off: "Fuera de grabación — no miro ni escucho. Toca Reanudar cuando estés listo.", on: "Volvemos a grabar.", still: "Sigue fuera de grabación — toca Reanudar cuando estés listo." },
};
export function offRecordLine(lang: string, k: "off" | "on" | "still") {
  return (OFF_LINES[(lang || "en").slice(0, 2)] ?? OFF_LINES.en)[k];
}
