"use client";
/**
 * Claros live session store (zustand). The UI subscribes here for everything
 * coming from the voice agent (client tools) and the server WS.
 *
 *   import { useClaros } from "@/voice/store";
 *   const step = useClaros((s) => s.highlightedStepId);
 */
import { create } from "zustand";
import type {
  ActivityKind, ClientToolName, Mode, ClientToolParams, LedgerMsg, Moment, ScreenEvent, ServerMsg,
} from "@/lib/contracts";

export type OrbState =
  | "listening" | "noticing" | "asking" | "speaking" | "off_record" | "away" | "reconnecting" | "idle";

export interface TranscriptLine {
  id: string;
  role: "user" | "agent";
  text: string;
  t: number;
}

export interface ToolEvent<K extends ClientToolName = ClientToolName> {
  name: K;
  params: ClientToolParams[K];
  t: number;
}

export interface ClarosState {
  sessionId: string | null;
  wsStatus: "idle" | "connecting" | "open" | "closed";
  voiceStatus: "disconnected" | "connecting" | "connected" | "error";
  agentMode: "speaking" | "listening";
  userSpeaking: boolean;
  offRecord: boolean;
  /** current session phase (from hello mode, updated by server `phase`); UI routes on this */
  phase: Mode | null;
  /** set when the session ended (server control end_task or local end) */
  sessionEnded: boolean;
  activity: ActivityKind;
  /** Date.now() of the last activity report from the capture worker */
  activityT: number;
  captureActive: boolean;
  keyframesSent: number;
  /** last asked/intervene text (for captions) */
  caption: string;
  transcript: TranscriptLine[];
  events: ScreenEvent[];
  ledger: Omit<LedgerMsg, "type"> | null;
  highlightedStepId: string | null;
  moment: Moment | null;
  openMapId: string | null;
  expertRequest: { workflow_hint: string; t: number } | null;
  toolLog: ToolEvent[];
  serverLog: ServerMsg[];
  /** "noticing" pulse: incremented when new events arrive */
  curiousCount: number;
  set: (p: Partial<ClarosState>) => void;
  pushTranscript: (l: TranscriptLine) => void;
  pushTool: <K extends ClientToolName>(name: K, params: ClientToolParams[K]) => void;
  pushServer: (m: ServerMsg) => void;
  reset: () => void;
}

const initial = {
  sessionId: null,
  wsStatus: "idle" as const,
  voiceStatus: "disconnected" as const,
  agentMode: "listening" as const,
  userSpeaking: false,
  offRecord: false,
  phase: null as Mode | null,
  sessionEnded: false,
  activity: "idle" as ActivityKind,
  activityT: 0,
  captureActive: false,
  keyframesSent: 0,
  caption: "",
  transcript: [] as TranscriptLine[],
  events: [] as ScreenEvent[],
  ledger: null,
  highlightedStepId: null,
  moment: null,
  openMapId: null,
  expertRequest: null,
  toolLog: [] as ToolEvent[],
  serverLog: [] as ServerMsg[],
  curiousCount: 0,
};

export const useClaros = create<ClarosState>((set) => ({
  ...initial,
  set: (p) => set(p),
  pushTranscript: (l) => set((s) => ({ transcript: [...s.transcript.slice(-199), l] })),
  pushTool: (name, params) =>
    set((s) => {
      const ev = { name, params, t: Date.now() } as ToolEvent;
      const patch: Partial<ClarosState> = { toolLog: [...s.toolLog.slice(-99), ev] };
      if (name === "highlight_step") patch.highlightedStepId = (params as ClientToolParams["highlight_step"]).step_id;
      if (name === "show_moment") {
        const p = params as ClientToolParams["show_moment"];
        patch.moment = { session_id: s.sessionId ?? "", keyframe_ids: p.keyframe_ids ?? [], t: p.t ?? 0, utterance_ids: [] };
      }
      if (name === "go_off_record") patch.offRecord = true;
      if (name === "go_on_record") patch.offRecord = false;
      if (name === "open_map") patch.openMapId = (params as ClientToolParams["open_map"]).workflow_id;
      if (name === "request_expert")
        patch.expertRequest = { workflow_hint: (params as ClientToolParams["request_expert"]).workflow_hint, t: Date.now() };
      return patch;
    }),
  pushServer: (m) =>
    set((s) => {
      const patch: Partial<ClarosState> = { serverLog: [...s.serverLog.slice(-199), m] };
      switch (m.type) {
        case "events":
          patch.events = [...s.events, ...m.items].slice(-500);
          patch.curiousCount = s.curiousCount + m.items.length;
          break;
        case "ledger":
          patch.ledger = { open: m.open, saved_for_later: m.saved_for_later, top: m.top };
          break;
        case "ask":
        case "intervene":
          patch.caption = m.text;
          if (m.type === "intervene") patch.moment = m.moment;
          break;
        case "show_moment":
          patch.moment = m.moment;
          break;
        case "highlight_step":
          patch.highlightedStepId = m.step_id;
          break;
        case "phase":
          patch.phase = m.phase;
          break;
        case "status":
          if (m.text === "debrief_ready") patch.phase = "debrief";
          break;
        case "control":
          if (m.action === "off_record_on") patch.offRecord = true;
          if (m.action === "off_record_off") patch.offRecord = false;
          if (m.action === "end_task") patch.sessionEnded = true;
          break;
      }
      return patch;
    }),
  reset: () => set({ ...initial }),
}));
