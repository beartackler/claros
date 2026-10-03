"use client";
/**
 * useClarosVoice — ElevenLabs agent session bound to a Claros session.
 *
 * - token: GET {api}/api/el/token?agent=claros  (server holds the API key)
 * - customLlmExtraBody {session_id, mode} + dynamicVariables {session_id, mode, lang, user_name}
 * - forwards final transcripts → `utterance`, VAD crossings → `vad`, mode → `agent_state`
 * - server `ask` / `intervene` → sendUserMessage("⟦ask:ID⟧" / "⟦intervene:ID⟧"), `context_update` → sendContextualUpdate
 * - sendUserActivity() ≤1/s while the capture worker reports typing/scrolling
 * - client tools → zustand store (useClaros)
 * Must be rendered under <ConversationProvider> (see voice/providers.tsx).
 */
import { useCallback, useEffect, useRef } from "react";
import { useConversation } from "@elevenlabs/react";
import { API_BASE, type ClientToolParams, type Mode } from "@/lib/contracts";
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

export function useClarosVoice({ sessionId, mode, lang, userName = "", workflowName = "this task", agent = "claros" }: ClarosVoiceOptions) {
  const set = useClaros((s) => s.set);
  const offRecord = useClaros((s) => s.offRecord);
  const vadSpeaking = useRef(false);
  const vadStart = useRef(0);
  const agentSpeakStart = useRef(0);
  const lastUserActivity = useRef(0);
  const statusRef = useRef<string>("disconnected");

  const conv = useConversation({
    onStatusChange: ({ status }) => {
      statusRef.current = status;
      set({ voiceStatus: status === "connected" ? "connected" : status === "connecting" ? "connecting" : "disconnected" });
    },
    onError: (message) => {
      set({ voiceStatus: "error" });
      useClaros.getState().pushServer({ type: "status", level: "error", text: `voice: ${message}` });
    },
    onModeChange: ({ mode: m }) => {
      const t = now();
      if (m === "speaking") agentSpeakStart.current = t;
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

  // ---- off-record = mic muted + control message ----
  const setOffRecord = useCallback(
    (on: boolean) => {
      set({ offRecord: on });
      try { convRef.current.setMuted(on); } catch {}
      sendControl(on ? "off_record_on" : "off_record_off");
    },
    [set],
  );
  useEffect(() => {
    if (connected()) {
      try { convRef.current.setMuted(offRecord); } catch {}
    }
  }, [offRecord]);

  // ---- server → agent ----
  useEffect(() => {
    let offs: (() => void)[] = [];
    const attach = () => {
      offs.forEach((f) => f());
      offs = [];
      const s = getSocket();
      if (!s) return;
      offs.push(
        s.on("ask", (m) => { if (connected()) convRef.current.sendUserMessage(`⟦ask:${m.unknown_id}|${m.text}⟧`); }),
        s.on("intervene", (m) => { if (connected()) convRef.current.sendUserMessage(`⟦intervene:${m.guardrail_id}|${m.text}⟧`); }),
        // brain-originated control (expert said it by voice): store already mirrors it; don't echo back
        s.on("control", (m) => {
          if (m.action === "off_record_on" || m.action === "off_record_off") {
            try { convRef.current.setMuted(m.action === "off_record_on"); } catch {}
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
  }, []);

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
    go_on_record: () => {
      useClaros.getState().pushTool("go_on_record", {});
      setOffRecordRef.current(false);
      return "on record";
    },
    open_map: (p: ClientToolParams["open_map"]) => {
      useClaros.getState().pushTool("open_map", p);
      return "ok";
    },
    request_expert: (p: ClientToolParams["request_expert"]) => {
      useClaros.getState().pushTool("request_expert", p);
      return "request created";
    },
  });
  const setOffRecordRef = useRef(setOffRecord);
  setOffRecordRef.current = setOffRecord;

  const start = useCallback(async () => {
    if (!sessionId) throw new Error("no session");
    set({ voiceStatus: "connecting" });
    await navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => s.getTracks().forEach((t) => t.stop())).catch(() => {
      throw new Error("Microphone permission denied");
    });
    const common = {
      customLlmExtraBody: { session_id: sessionId, mode },
      dynamicVariables: { session_id: sessionId, mode, lang, user_name: userName, workflow_name: workflowName },
      clientTools: clientTools.current as unknown as Record<string, (p: unknown) => string>,
    };
    let tok: TokenResp = {};
    try {
      tok = await fetchToken(agent);
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
