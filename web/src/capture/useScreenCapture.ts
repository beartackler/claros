"use client";
/**
 * useScreenCapture(): window share -> capture worker -> WS (activity + keyframe).
 *
 *   const cap = useScreenCapture();
 *   <button onClick={cap.start}>Share window</button>   // must be a user gesture
 *
 * Frames: MediaStreamTrackProcessor readable is transferred to a worker (Chrome).
 * Fallback: hidden <video> + createImageBitmap, pulled on worker-driven ticks
 * (no rAF/main-thread timers — the tab is backgrounded while the user works).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ActivityKind, KeyframeMsg } from "@/lib/contracts";
import { toSession } from "@/lib/clock";
import { getSocket } from "@/lib/ws";
import { useClaros } from "@/voice/store";
import type { WorkerIn, WorkerKeyframe, WorkerOut, WorkerStats } from "./types";

/** Every screen share this tab holds, whichever page started it: the debrief/exit can always end them all. */
const LIVE_STREAMS = new Set<MediaStream>();
export function stopAllScreenCapture() {
  LIVE_STREAMS.forEach((st) => st.getTracks().forEach((t) => t.stop()));
  LIVE_STREAMS.clear();
}

export type DisplaySurface = "window" | "browser" | "monitor" | "unknown";

export interface CaptureOptions {
  /** forward activity/keyframes to the current WS session (default true) */
  send?: boolean;
  onKeyframe?: (kf: KeyframeMsg) => void;
  onActivity?: (kind: ActivityKind) => void;
  /** max bytes buffered on the socket before non-boundary keyframes are dropped */
  maxBuffered?: number;
}

export interface CaptureState {
  active: boolean;
  surface: DisplaySurface;
  dims: [number, number];
  activity: ActivityKind;
  away: boolean;
  error: string | null;
  keyframes: number;
  dropped: number;
  lastKeyframe: { url: string; reason: string; seq: number; bytes: number; tiles: number } | null;
  stats: WorkerStats | null;
  pipeline: "track-processor" | "video-fallback" | null;
}

const INITIAL: CaptureState = {
  active: false, surface: "unknown", dims: [0, 0], activity: "idle", away: false, error: null,
  keyframes: 0, dropped: 0, lastKeyframe: null, stats: null, pipeline: null,
};

type MSTP = new (init: { track: MediaStreamTrack }) => { readable: ReadableStream<VideoFrame> };

export function useScreenCapture(opts: CaptureOptions = {}) {
  const [state, setState] = useState<CaptureState>(INITIAL);
  const streamRef = useRef<MediaStream | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const optsRef = useRef(opts);
  optsRef.current = opts;
  const setStore = useClaros((s) => s.set);

  const post = (m: WorkerIn, transfer: Transferable[] = []) => workerRef.current?.postMessage(m, transfer);

  const stop = useCallback(() => {
    post({ type: "stop" });
    workerRef.current?.terminate();
    workerRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    if (streamRef.current) LIVE_STREAMS.delete(streamRef.current);
    streamRef.current = null;
    if (videoRef.current) {
      videoRef.current.srcObject = null;
      videoRef.current = null;
    }
    setState((s) => ({ ...s, active: false, activity: "idle" }));
    setStore({ captureActive: false, activity: "idle" });
  }, [setStore]);

  const handleKeyframe = useCallback(
    (m: WorkerKeyframe) => {
      const msg: KeyframeMsg = {
        type: "keyframe", t: toSession(m.t), seq: m.seq, reason: m.reason,
        jpeg_b64: m.jpeg_b64, dims: m.dims, changed_tiles: m.changed_tiles,
      };
      const sock = getSocket();
      const o = optsRef.current;
      let dropped = false;
      if (useClaros.getState().offRecord) dropped = true; // off the record: nothing leaves the browser
      else if (o.send !== false && sock) {
        const tooBusy = sock.buffered > (o.maxBuffered ?? 4_000_000);
        if (tooBusy && m.reason !== "boundary" && m.reason !== "toast") dropped = true;
        else sock.send(msg);
      }
      o.onKeyframe?.(msg);
      setState((s) => ({
        ...s,
        keyframes: s.keyframes + (dropped ? 0 : 1),
        dropped: s.dropped + (dropped ? 1 : 0),
        lastKeyframe: {
          url: `data:image/jpeg;base64,${m.jpeg_b64}`, reason: m.reason, seq: m.seq, bytes: m.bytes,
          tiles: m.changed_tiles.length,
        },
      }));
      if (!dropped) useClaros.setState((s) => ({ keyframesSent: s.keyframesSent + 1 }));
    },
    [],
  );

  const start = useCallback(async () => {
    if (streamRef.current) return;
    setState({ ...INITIAL });
    let stream: MediaStream;
    try {
      // Non-standard-but-supported Chrome options are passed through as-is.
      const constraints = {
        video: { displaySurface: "window", frameRate: { max: 5 } },
        audio: false,
        selfBrowserSurface: "exclude",
        surfaceSwitching: "include",
        monitorTypeSurfaces: "exclude",
        systemAudio: "exclude",
      } as DisplayMediaStreamOptions;
      stream = await navigator.mediaDevices.getDisplayMedia(constraints);
    } catch (e) {
      setState((s) => ({ ...s, error: (e as Error).message || "Screen share was cancelled" }));
      return;
    }
    streamRef.current = stream;
    LIVE_STREAMS.add(stream);
    const track = stream.getVideoTracks()[0];
    try { track.contentHint = "detail"; } catch {}
    const settings = track.getSettings() as MediaTrackSettings & { displaySurface?: string };
    const surface = (settings.displaySurface as DisplaySurface) ?? "unknown";

    const worker = new Worker(new URL("./capture.worker.ts", import.meta.url), { type: "module" });
    workerRef.current = worker;

    worker.onmessage = async (ev: MessageEvent<WorkerOut>) => {
      const m = ev.data;
      switch (m.type) {
        case "keyframe":
          handleKeyframe(m);
          break;
        case "activity": {
          const sock = getSocket();
          if (optsRef.current.send !== false && sock && !useClaros.getState().offRecord)
            sock.send({ type: "activity", t: toSession(m.t), kind: m.kind, tiles_changed: m.tiles_changed, dims: m.dims });
          optsRef.current.onActivity?.(m.kind);
          setState((s) => (s.activity === m.kind ? s : { ...s, activity: m.kind }));
          setStore({ activity: m.kind, activityT: Date.now() });
          break;
        }
        case "stats":
          setState((s) => ({
            ...s, stats: m,
            dims: s.dims[0] === m.dims[0] && s.dims[1] === m.dims[1] ? s.dims : m.dims,
          }));
          break;
        case "tick": {
          const v = videoRef.current;
          if (!v || v.readyState < 2 || !v.videoWidth) break;
          try {
            const bmp = await createImageBitmap(v);
            post({ type: "bitmap", bitmap: bmp }, [bmp]);
          } catch {}
          break;
        }
        case "ended":
          break;
        case "error":
          setState((s) => ({ ...s, error: m.message }));
          break;
      }
    };

    const Processor = (globalThis as unknown as { MediaStreamTrackProcessor?: MSTP }).MediaStreamTrackProcessor;
    let pipeline: CaptureState["pipeline"] = null;
    let processorOk = false;
    if (Processor) {
      try {
        const proc = new Processor({ track });
        post({ type: "stream", readable: proc.readable }, [proc.readable as unknown as Transferable]);
        processorOk = true;
        pipeline = "track-processor";
      } catch {
        processorOk = false;
      }
    }
    if (!processorOk) {
      const v = document.createElement("video");
      v.muted = true;
      v.playsInline = true;
      v.srcObject = stream;
      await v.play().catch(() => {});
      videoRef.current = v;
      post({ type: "start_fallback", fps: 4 });
      pipeline = "video-fallback";
    }

    const setAway = (away: boolean) => {
      post({ type: "away", away });
      setState((s) => ({ ...s, away, activity: away ? "away" : s.activity }));
    };
    track.addEventListener("mute", () => setAway(true)); // minimized / occluded / source paused
    track.addEventListener("unmute", () => setAway(false));
    track.addEventListener("ended", () => {
      // user clicked "Stop sharing" (or window closed)
      const sock = getSocket();
      sock?.send({ type: "activity", t: toSession(performance.timeOrigin + performance.now()), kind: "away", tiles_changed: 0, dims: [0, 0] });
      stop();
    });

    setState((s) => ({
      ...s, active: true, surface, pipeline,
      dims: [settings.width ?? 0, settings.height ?? 0],
      error: surface !== "window" && surface !== "unknown"
        ? `You shared a ${surface}. Claros works best when you share a single window.` : null,
    }));
    setStore({ captureActive: true });
  }, [handleKeyframe, setStore, stop]);

  /** Force a keyframe now (e.g. user said "look at this"). */
  const snap = useCallback((reason: KeyframeMsg["reason"] = "boundary") => post({ type: "force", reason }), []);

  useEffect(() => () => stop(), [stop]);
  useEffect(
    () => useClaros.subscribe((s, prev) => {
      if (s.sessionEnded && !prev.sessionEnded && streamRef.current) stop();
    }),
    [stop],
  );

  return { ...state, start, stop, snap, stream: streamRef };
}
