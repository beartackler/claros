import type { ActivityKind, KeyframeReason, Rect } from "@/lib/contracts";

export type WorkerIn =
  | { type: "stream"; readable: ReadableStream<VideoFrame> }
  | { type: "start_fallback"; fps?: number }
  | { type: "bitmap"; bitmap: ImageBitmap }
  | { type: "away"; away: boolean }
  | { type: "force"; reason?: KeyframeReason }
  | { type: "stop" };

export interface WorkerKeyframe {
  type: "keyframe";
  t: number; // local epoch ms (performance.timeOrigin + now) — convert with clock.toSession
  seq: number;
  reason: KeyframeReason;
  jpeg_b64: string;
  dims: [number, number];
  changed_tiles: Rect[];
  bytes: number;
}

export interface WorkerActivity {
  type: "activity";
  t: number;
  kind: ActivityKind;
  tiles_changed: number;
  rects: Rect[];
  dims: [number, number];
}

export interface WorkerStats {
  type: "stats";
  t: number;
  tiles_changed: number;
  dhash_dist: number;
  kind: string;
  fps: number;
  dims: [number, number];
}

export type WorkerOut =
  | WorkerKeyframe
  | WorkerActivity
  | WorkerStats
  | { type: "tick" }
  | { type: "ended" }
  | { type: "error"; message: string };
