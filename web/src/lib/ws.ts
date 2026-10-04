/**
 * Reconnecting WebSocket client for ws://.../ws/session/{id}.
 * - sends `hello` then 3x `clock_sync` on every (re)connect
 * - outbound queue while disconnected (bounded; stale keyframes/activity dropped)
 * - typed handlers: socket.on("ask", (m) => ...)
 */
import { WS_BASE, type ClientMsg, type HelloMsg, type ServerMsg, type ServerMsgOf, type ServerMsgType } from "./contracts";
import { localNow, onClockSync } from "./clock";

type Handler<T extends ServerMsgType> = (msg: ServerMsgOf<T>) => void;
export type SocketStatus = "idle" | "connecting" | "open" | "closed";

const MAX_QUEUE = 200;
const MAX_QUEUED_KEYFRAMES = 3;

export class ClarosSocket {
  readonly sessionId: string;
  private hello: Omit<HelloMsg, "type">;
  private ws: WebSocket | null = null;
  private queue: ClientMsg[] = [];
  private handlers = new Map<string, Set<(m: ServerMsg) => void>>();
  private anyHandlers = new Set<(m: ServerMsg) => void>();
  private statusHandlers = new Set<(s: SocketStatus) => void>();
  private retry = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private syncTimer: ReturnType<typeof setInterval> | null = null;
  private closedByUser = false;
  status: SocketStatus = "idle";

  constructor(hello: Omit<HelloMsg, "type">, private base: string = WS_BASE) {
    this.sessionId = hello.session_id;
    this.hello = hello;
  }

  connect() {
    this.closedByUser = false;
    this.open();
    return this;
  }

  private setStatus(s: SocketStatus) {
    this.status = s;
    this.statusHandlers.forEach((h) => h(s));
  }

  private open() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    this.setStatus("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(`${this.base}/ws/session/${encodeURIComponent(this.sessionId)}`);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.setStatus("open");
      this.rawSend({ type: "hello", ...this.hello });
      this.syncClock();
      setTimeout(() => this.syncClock(), 300);
      setTimeout(() => this.syncClock(), 900);
      if (this.syncTimer) clearInterval(this.syncTimer);
      this.syncTimer = setInterval(() => this.syncClock(), 30_000);
      const q = this.queue;
      this.queue = [];
      q.forEach((m) => this.rawSend(m));
    };
    ws.onmessage = (ev) => {
      let msg: ServerMsg;
      try {
        msg = JSON.parse(typeof ev.data === "string" ? ev.data : "");
      } catch {
        return;
      }
      if (msg.type === "clock_sync" && "server_t" in msg) onClockSync(msg.client_t, msg.server_t);
      this.handlers.get(msg.type)?.forEach((h) => h(msg));
      this.anyHandlers.forEach((h) => h(msg));
    };
    ws.onclose = () => {
      if (this.syncTimer) clearInterval(this.syncTimer);
      this.ws = null;
      this.setStatus("closed");
      if (!this.closedByUser) this.scheduleReconnect();
    };
    ws.onerror = () => {
      /* onclose follows */
    };
  }

  private scheduleReconnect() {
    if (this.timer) return;
    const delay = Math.min(10_000, 500 * 2 ** this.retry) + Math.random() * 250;
    this.retry++;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.open();
    }, delay);
  }

  syncClock() {
    this.rawSend({ type: "clock_sync", client_t: localNow() });
  }

  private rawSend(msg: ClientMsg): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  /** Same session, new phase (capture → debrief): the server must hear it, or it never starts the debrief. */
  rehello(hello: Omit<HelloMsg, "type">) {
    this.hello = { ...this.hello, ...hello };
    this.rawSend({ type: "hello", ...this.hello }); // if closed, onopen sends the updated hello
  }

  /** Send now, or queue until (re)connected. */
  send(msg: ClientMsg) {
    if (this.rawSend(msg)) return;
    if (msg.type === "clock_sync" || msg.type === "hello") return;
    if (msg.type === "activity") this.queue = this.queue.filter((m) => m.type !== "activity");
    if (msg.type === "keyframe") {
      const kfs = this.queue.filter((m) => m.type === "keyframe");
      if (kfs.length >= MAX_QUEUED_KEYFRAMES) {
        const drop = kfs[0];
        this.queue = this.queue.filter((m) => m !== drop);
      }
    }
    this.queue.push(msg);
    if (this.queue.length > MAX_QUEUE) this.queue.splice(0, this.queue.length - MAX_QUEUE);
  }

  /** Bytes buffered on the socket (use to back off keyframes). */
  get buffered(): number {
    return this.ws?.bufferedAmount ?? 0;
  }

  on<T extends ServerMsgType>(type: T, h: Handler<T>): () => void {
    let set = this.handlers.get(type);
    if (!set) this.handlers.set(type, (set = new Set()));
    const fn = h as (m: ServerMsg) => void;
    set.add(fn);
    return () => set!.delete(fn);
  }

  onAny(h: (m: ServerMsg) => void): () => void {
    this.anyHandlers.add(h);
    return () => this.anyHandlers.delete(h);
  }

  onStatus(h: (s: SocketStatus) => void): () => void {
    this.statusHandlers.add(h);
    return () => this.statusHandlers.delete(h);
  }

  close() {
    this.closedByUser = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.syncTimer) clearInterval(this.syncTimer);
    this.timer = null;
    this.ws?.close();
    this.ws = null;
    this.setStatus("closed");
  }
}

// ---- module-level "current socket" so voice/capture/UI share one connection ----
let current: ClarosSocket | null = null;
const currentListeners = new Set<(s: ClarosSocket | null) => void>();

export function connectSession(hello: Omit<HelloMsg, "type">): ClarosSocket {
  if (current && current.sessionId === hello.session_id) {
    current.rehello(hello);
    return current;
  }
  current?.close();
  current = new ClarosSocket(hello).connect();
  currentListeners.forEach((l) => l(current));
  return current;
}

export function getSocket(): ClarosSocket | null {
  return current;
}

export function disconnectSession() {
  current?.close();
  current = null;
  currentListeners.forEach((l) => l(null));
}

export function onSocketChange(l: (s: ClarosSocket | null) => void): () => void {
  currentListeners.add(l);
  return () => currentListeners.delete(l);
}
