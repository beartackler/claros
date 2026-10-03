/// <reference lib="webworker" />
/**
 * Claros capture worker.
 *
 * Input: a ReadableStream<VideoFrame> (MediaStreamTrackProcessor, transferred) or
 * ImageBitmaps pushed by the main thread (fallback; the worker drives the clock via
 * `tick` messages because main-thread timers / rAF are throttled in background tabs).
 *
 * Per frame: downscale to 256x144 gray -> 16x9 tile mean-abs-diff + 64x36 dHash +
 * row-profile shift (scroll). Classifies typing / scrolling / navigating / idle and
 * fires screenpipe-style keyframes (settle / boundary / heartbeat / toast).
 * Every VideoFrame is closed.
 */
import type { ActivityKind, KeyframeReason, Rect } from "@/lib/contracts";
import type { WorkerIn, WorkerOut } from "./types";

declare const self: DedicatedWorkerGlobalScope;

// ---- tunables ----
const AW = 256, AH = 144; // analysis resolution
const TX = 16, TY = 9; // tile grid
const TW = AW / TX, TH = AH / TY; // 16x16 px tiles
const TILE_T = 0.8; // mean abs diff (0..255) for a tile to count as changed
const MIN_FRAME_GAP = 180; // ms between analysed frames (≈5 fps)
const SETTLE: Record<string, number> = { typing: 500, scrolling: 400, navigating: 300, other: 300 };
const MIN_KF_GAP = 200;
const HEARTBEAT = 10_000;
const IDLE_AFTER = 1000;
const MAX_LONG_EDGE = 1600;
const JPEG_Q = 0.75;
const ACTIVITY_MIN_GAP = 500;

const now = () => performance.timeOrigin + performance.now();
const post = (m: WorkerOut, transfer: Transferable[] = []) => self.postMessage(m, transfer);

// ---- state ----
type Src = VideoFrame | ImageBitmap;
let lastSrc: Src | null = null;
let lastSrcDims: [number, number] = [0, 0];
let lastAnalysedT = 0;
let prevGray: Float32Array | null = null;
let kfRefGray: Float32Array | null = null; // gray at last keyframe (for changed_tiles)
let prevProfileFrame: Float32Array | null = null;
let lastChangeT = 0;
let busyKind: "typing" | "scrolling" | "navigating" | "other" = "other";
let dirty = false;
let lastKfT = 0;
let seq = 0;
let encoding = false;
let pendingBoundary: { at: number; reason: KeyframeReason } | null = null;
let smallChangeTimes: number[] = [];
let reportedKind: ActivityKind = "idle";
let lastActivityPost = 0;
let lastTilesChanged = 0;
let away = false;
let running = false;
let fallbackTick: ReturnType<typeof setInterval> | null = null;
let frames = 0, fpsWindowStart = 0, fps = 0;

const aCanvas = new OffscreenCanvas(AW, AH);
const aCtx = aCanvas.getContext("2d", { willReadFrequently: true })!;
const kCanvas = new OffscreenCanvas(16, 16);
const kCtx = kCanvas.getContext("2d")!;

function dimsOf(src: Src): [number, number] {
  if ("displayWidth" in src) return [src.displayWidth, src.displayHeight];
  return [src.width, src.height];
}

function toGray(src: Src): Float32Array {
  aCtx.drawImage(src as CanvasImageSource, 0, 0, AW, AH);
  const d = aCtx.getImageData(0, 0, AW, AH).data;
  const g = new Float32Array(AW * AH);
  for (let i = 0, j = 0; i < g.length; i++, j += 4) g[i] = 0.299 * d[j] + 0.587 * d[j + 1] + 0.114 * d[j + 2];
  return g;
}

/** 64x36 dHash bits from the 256x144 gray (4x4 box downsample). */
function dhash(g: Float32Array): Uint8Array {
  const W = 64, H = 36, s = 4;
  const small = new Float32Array(W * H);
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      let acc = 0;
      for (let dy = 0; dy < s; dy++) for (let dx = 0; dx < s; dx++) acc += g[(y * s + dy) * AW + x * s + dx];
      small[y * W + x] = acc;
    }
  const bits = new Uint8Array((W - 1) * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W - 1; x++) bits[y * (W - 1) + x] = small[y * W + x] < small[y * W + x + 1] ? 1 : 0;
  return bits;
}
let prevHash: Uint8Array | null = null;
function hamming(a: Uint8Array, b: Uint8Array) {
  let d = 0;
  for (let i = 0; i < a.length; i++) d += a[i] ^ b[i];
  return d / a.length;
}

function tileDiff(a: Float32Array, b: Float32Array): Float32Array {
  const out = new Float32Array(TX * TY);
  for (let ty = 0; ty < TY; ty++)
    for (let tx = 0; tx < TX; tx++) {
      let acc = 0;
      for (let y = ty * TH; y < (ty + 1) * TH; y++) {
        const row = y * AW;
        for (let x = tx * TW; x < (tx + 1) * TW; x++) acc += Math.abs(a[row + x] - b[row + x]);
      }
      out[ty * TX + tx] = acc / (TW * TH);
    }
  return out;
}

function changedIdx(diff: Float32Array): number[] {
  const idx: number[] = [];
  for (let i = 0; i < diff.length; i++) if (diff[i] > TILE_T) idx.push(i);
  return idx;
}

/** Row-profile shift detection restricted to the column band of changed tiles. */
function detectScroll(a: Float32Array, b: Float32Array, idx: number[]): number {
  if (idx.length < 4) return 0;
  let minX = TX, maxX = -1;
  const rows = new Set<number>();
  for (const i of idx) {
    const tx = i % TX;
    minX = Math.min(minX, tx);
    maxX = Math.max(maxX, tx);
    rows.add(Math.floor(i / TX));
  }
  if (rows.size < 3) return 0; // scrolling changes many rows
  const x0 = minX * TW, x1 = (maxX + 1) * TW;
  const prof = (g: Float32Array) => {
    const p = new Float32Array(AH);
    for (let y = 0; y < AH; y++) {
      let acc = 0;
      for (let x = x0; x < x1; x++) acc += g[y * AW + x];
      p[y] = acc / (x1 - x0);
    }
    return p;
  };
  const pa = prof(a), pb = prof(b);
  const err = (s: number) => {
    let acc = 0, n = 0;
    for (let y = 0; y < AH; y++) {
      const y2 = y + s;
      if (y2 < 0 || y2 >= AH) continue;
      acc += Math.abs(pa[y2] - pb[y]);
      n++;
    }
    return n > AH / 2 ? acc / n : Infinity;
  };
  const e0 = err(0);
  if (e0 < 0.5) return 0;
  let best = 0, bestE = e0;
  for (let s = -36; s <= 36; s++) {
    if (s === 0) continue;
    const e = err(s);
    if (e < bestE) {
      bestE = e;
      best = s;
    }
  }
  return bestE < e0 * 0.55 ? best : 0;
}

/** Toast-like: compact new cluster of tiles (not typing), ideally near an edge. */
function toastLike(idx: number[], t: number): KeyframeReason | null {
  if (idx.length < 3 || idx.length > 24) return null;
  if (smallChangeTimes.some((st) => t - st < 1000)) return null;
  let minX = TX, maxX = -1, minY = TY, maxY = -1;
  for (const i of idx) {
    const x = i % TX, y = Math.floor(i / TX);
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  const area = (maxX - minX + 1) * (maxY - minY + 1);
  if (area > TX * TY * 0.2 || idx.length / area < 0.5) return null;
  const nearEdge = minX === 0 || maxX === TX - 1 || minY === 0 || maxY === TY - 1;
  return nearEdge ? "toast" : "boundary";
}

function setLastSrc(src: Src) {
  lastSrc?.close();
  lastSrc = src;
}

function analyse(src: Src) {
  const t = now();
  const dims = dimsOf(src);
  const dimsChanged = dims[0] !== lastSrcDims[0] || dims[1] !== lastSrcDims[1];
  lastSrcDims = dims;
  const g = toGray(src);
  setLastSrc(src); // retain latest frame for settle keyframes (screen may stop producing frames)

  frames++;
  if (t - fpsWindowStart > 2000) {
    fps = (frames * 1000) / (t - fpsWindowStart);
    frames = 0;
    fpsWindowStart = t;
  }

  if (!prevGray || dimsChanged) {
    prevGray = g;
    prevHash = dhash(g);
    if (dimsChanged && kfRefGray) {
      kfRefGray = null;
    }
    dirty = true;
    lastChangeT = t;
    busyKind = "navigating";
    pendingBoundary = { at: t + 300, reason: "boundary" };
    return;
  }

  const diff = tileDiff(prevGray, g);
  const idx = changedIdx(diff);
  const h = dhash(g);
  const hd = prevHash ? hamming(prevHash, h) : 1;
  prevHash = h;
  let kind: typeof busyKind | null = null;

  if (idx.length > 0) {
    const scroll = detectScroll(prevGray, g, idx);
    if (scroll !== 0) kind = "scrolling";
    else if (idx.length > TX * TY * 0.3 || hd > 0.25) kind = "navigating";
    else if (idx.length <= 3) {
      smallChangeTimes = smallChangeTimes.filter((st) => t - st < 1500);
      smallChangeTimes.push(t);
      kind = smallChangeTimes.length >= 2 ? "typing" : "other";
    } else {
      const tl = toastLike(idx, t);
      if (tl) pendingBoundary = { at: t + 250, reason: tl }; // let it finish animating in
      kind = "other";
    }
    lastChangeT = t;
    busyKind = kind;
    dirty = true;
  }
  prevGray = g;
  lastTilesChanged = idx.length;

  post({ type: "stats", t, tiles_changed: idx.length, dhash_dist: hd, kind: kind ?? "none", fps, dims });
  maybeReportActivity(t);
}

function currentKind(t: number): ActivityKind {
  if (away) return "away";
  if (t - lastChangeT > IDLE_AFTER) return "idle";
  return busyKind === "other" ? "navigating" : busyKind;
}

function maybeReportActivity(t: number) {
  const k = currentKind(t);
  const busy = k === "typing" || k === "scrolling" || k === "navigating";
  if (k !== reportedKind || busy) {
    if (t - lastActivityPost < ACTIVITY_MIN_GAP) return; // ≤2/s; tick() retries
    reportedKind = k;
    lastActivityPost = t;
    post({ type: "activity", t, kind: k, tiles_changed: lastTilesChanged, dims: lastSrcDims });
  }
}

function tilesToRects(diff: Float32Array, kw: number, kh: number): Rect[] {
  const sx = kw / TX, sy = kh / TY;
  const rects: Rect[] = [];
  for (let ty = 0; ty < TY; ty++) {
    let run = -1;
    for (let tx = 0; tx <= TX; tx++) {
      const on = tx < TX && diff[ty * TX + tx] > TILE_T;
      if (on && run < 0) run = tx;
      if (!on && run >= 0) {
        rects.push([Math.round(run * sx), Math.round(ty * sy), Math.round((tx - run) * sx), Math.round(sy)]);
        run = -1;
      }
    }
  }
  return rects;
}

async function emitKeyframe(reason: KeyframeReason) {
  if (!lastSrc || encoding) return;
  const t = now();
  const [w, h] = lastSrcDims;
  if (!w || !h) return;
  const scale = Math.min(1, MAX_LONG_EDGE / Math.max(w, h));
  const kw = Math.round(w * scale), kh = Math.round(h * scale);

  const g = prevGray;
  let rects: Rect[] = [[0, 0, kw, kh]];
  if (kfRefGray && g) {
    const r = tilesToRects(tileDiff(kfRefGray, g), kw, kh);
    if (r.length > 0) rects = r;
    else if (reason === "settle" || reason === "heartbeat") {
      dirty = false; // identical to last keyframe: skip
      return;
    }
  }

  encoding = true;
  try {
    if (kCanvas.width !== kw || kCanvas.height !== kh) {
      kCanvas.width = kw;
      kCanvas.height = kh;
    }
    kCtx.drawImage(lastSrc as CanvasImageSource, 0, 0, kw, kh); // sync draw before any await
    kfRefGray = g;
    dirty = false;
    lastKfT = t;
    const blob = await kCanvas.convertToBlob({ type: "image/jpeg", quality: JPEG_Q });
    const dataUrl = new FileReaderSync().readAsDataURL(blob);
    const jpeg_b64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
    post({ type: "keyframe", t, seq: seq++, reason, jpeg_b64, dims: [kw, kh], changed_tiles: rects, bytes: blob.size });
  } finally {
    encoding = false;
  }
}

function tick() {
  if (!running) return;
  const t = now();
  maybeReportActivity(t);
  if (!lastSrc || away) return;
  if (t - lastKfT < MIN_KF_GAP) return;
  if (seq === 0) {
    void emitKeyframe("boundary");
    return;
  }
  if (pendingBoundary && t >= pendingBoundary.at) {
    const r = pendingBoundary.reason;
    pendingBoundary = null;
    void emitKeyframe(r);
    return;
  }
  if (!dirty) return;
  const quiet = t - lastChangeT;
  if (quiet >= (SETTLE[busyKind] ?? 300)) {
    void emitKeyframe(busyKind === "navigating" ? "boundary" : "settle");
  } else if (t - lastKfT >= HEARTBEAT) {
    void emitKeyframe("heartbeat");
  }
}

async function pump(readable: ReadableStream<VideoFrame>) {
  const reader = readable.getReader();
  try {
    while (running) {
      const { value: frame, done } = await reader.read();
      if (done || !frame) break;
      const t = now();
      if (t - lastAnalysedT < MIN_FRAME_GAP) {
        frame.close();
        continue;
      }
      lastAnalysedT = t;
      try {
        analyse(frame); // takes ownership (retained as lastSrc, closed on replace)
      } catch (e) {
        frame.close();
        if (lastSrc === frame) lastSrc = null;
        post({ type: "error", message: String(e) });
      }
    }
  } finally {
    try { reader.releaseLock(); } catch {}
    post({ type: "ended" });
  }
}

let tickTimer: ReturnType<typeof setInterval> | null = null;

function start() {
  running = true;
  seq = 0;
  lastKfT = 0;
  prevGray = null;
  kfRefGray = null;
  fpsWindowStart = now();
  if (!tickTimer) tickTimer = setInterval(tick, 50);
}

function stop() {
  running = false;
  if (tickTimer) clearInterval(tickTimer);
  tickTimer = null;
  if (fallbackTick) clearInterval(fallbackTick);
  fallbackTick = null;
  lastSrc?.close();
  lastSrc = null;
}

self.onmessage = (ev: MessageEvent<WorkerIn>) => {
  const m = ev.data;
  switch (m.type) {
    case "stream":
      start();
      void pump(m.readable);
      break;
    case "start_fallback":
      start();
      // Worker timers are not throttled like background-tab main-thread timers.
      fallbackTick = setInterval(() => post({ type: "tick" }), 1000 / (m.fps ?? 4));
      break;
    case "bitmap":
      if (!running) {
        m.bitmap.close();
        break;
      }
      lastAnalysedT = now();
      try {
        analyse(m.bitmap);
      } catch (e) {
        m.bitmap.close();
        post({ type: "error", message: String(e) });
      }
      break;
    case "away":
      away = m.away;
      if (!away) {
        dirty = true;
        lastChangeT = now();
        pendingBoundary = { at: now() + 300, reason: "boundary" };
      }
      maybeReportActivity(now());
      break;
    case "force":
      void emitKeyframe(m.reason ?? "boundary");
      break;
    case "stop":
      stop();
      break;
  }
};
