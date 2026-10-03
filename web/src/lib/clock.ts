/**
 * Session clock. t = performance.timeOrigin + performance.now() - offset,
 * where offset is estimated from clock_sync round trips (min-RTT sample wins),
 * so client timestamps land on the server's clock.
 */
let offset = 0;
let bestRtt = Infinity;

export function localNow(): number {
  return performance.timeOrigin + performance.now();
}

/** ms on the session (server) clock */
export function now(): number {
  return localNow() - offset;
}

/** Convert a local Date.now()-style/epoch ms timestamp to session clock. */
export function toSession(localT: number): number {
  return localT - offset;
}

/** Feed a clock_sync reply {client_t, server_t}. */
export function onClockSync(client_t: number, server_t: number): void {
  const r = localNow();
  const rtt = r - client_t;
  if (rtt < 0 || rtt > 10_000) return;
  if (rtt <= bestRtt * 1.5 || bestRtt === Infinity) {
    if (rtt < bestRtt) bestRtt = rtt;
    offset = r - (server_t + rtt / 2);
  }
}

export function clockInfo() {
  return { offset, rtt: bestRtt };
}

export function resetClock() {
  offset = 0;
  bestRtt = Infinity;
}
