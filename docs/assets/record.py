"""Record the README demo GIFs/MP4s for Claros.

Prereqs: web dev server on :3000, API on :8787 (seeded), ffmpeg on PATH.
Run from the repo root:

    uv run --project server python docs/assets/record.py            # all three
    uv run --project server python docs/assets/record.py workmap     # just one (workmap | learner | expert)

Each scenario records a 1440x900 Playwright video, trims the page-load lead-in,
then encodes <name>.mp4 (H.264) and <name>.gif (two-pass palette, 1200px wide).
Nothing in the app is modified: demo states come from URL flags (?nudge=, ?demo=1, ?judge=1)
plus browser-side stubs for screen share / mic (so no permission prompts).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

WEB = "http://localhost:3000"
API = "http://localhost:8787"
OUT = Path(__file__).resolve().parent
W, H = 1440, 900
GIF_W, GIF_FPS = 1200, 15
WORKFLOW = "wf_ap_invoice"
MAX_GIF = 6_000_000

# Hide the Next.js dev indicator, draw a fake cursor (headless Chromium renders none).
INIT_JS = r"""
(() => {
  try { localStorage.setItem('claros.lang', 'en'); localStorage.setItem('claros.role', '__ROLE__'); } catch {}
  const css = `nextjs-portal{display:none!important}
    #__cursor{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
      transform:translate(-100px,-100px);will-change:transform}
    #__cursor svg{width:26px;height:26px;filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.35));transition:transform .12s ease}
    #__cursor.down svg{transform:scale(.82)}
    #__ring{position:fixed;left:0;top:0;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;
      border:3px solid #7c3aed;z-index:2147483646;pointer-events:none;opacity:0}
    #__ring.go{animation:__ring .45s ease-out}
    @keyframes __ring{from{opacity:.9;transform:scale(.4)}to{opacity:0;transform:scale(1.6)}}`;
  const add = () => {
    if (document.getElementById('__cursor')) return;
    const s = document.createElement('style'); s.textContent = css; document.documentElement.appendChild(s);
    const c = document.createElement('div'); c.id = '__cursor';
    c.innerHTML = '<svg viewBox="0 0 24 24"><path d="M4 2.5 L4 19.5 L8.6 15.3 L11.6 22 L14.6 20.7 L11.7 14.1 L18 14.1 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    const r = document.createElement('div'); r.id = '__ring';
    document.documentElement.appendChild(r); document.documentElement.appendChild(c);
    const p = window.__cursorPos || [-100, -100];
    c.style.transform = `translate(${p[0] - 4}px,${p[1] - 2}px)`;
    addEventListener('mousemove', e => { window.__cursorPos = [e.clientX, e.clientY]; c.style.transform = `translate(${e.clientX - 4}px,${e.clientY - 2}px)`; }, true);
    addEventListener('mousedown', e => { c.classList.add('down'); r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px'; r.classList.remove('go'); void r.offsetWidth; r.classList.add('go'); }, true);
    addEventListener('mouseup', () => c.classList.remove('down'), true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""

# Screen share + mic without prompts: a canvas "window" stream and a silent audio track.
MEDIA_STUB_JS = r"""
(() => {
  const md = navigator.mediaDevices; if (!md) return;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  md.getDisplayMedia = async () => {
    await wait(1100);
    const cv = document.createElement('canvas'); cv.width = 1280; cv.height = 800;
    const g = cv.getContext('2d'); g.fillStyle = '#f4f4f5'; g.fillRect(0, 0, 1280, 800);
    const s = cv.captureStream(2);
    const tr = s.getVideoTracks()[0]; const gs = tr.getSettings.bind(tr);
    tr.getSettings = () => ({ ...gs(), displaySurface: 'window' });
    return s;
  };
  // voice token never answers → "Claros says hi" stays active (no real voice session, no cost)
  const f = window.fetch.bind(window);
  window.fetch = (u, o) => String(u && u.url || u).includes('/api/el/token') ? new Promise(() => {}) : f(u, o);
  // demo drip ticks every 2.6 s in the app; run it faster so captions/why tags land within the clip
  const si = window.setInterval;
  window.setInterval = (fn, ms, ...a) => si(fn, ms === 2600 ? 800 : ms, ...a);
  // live socket never opens → the capture view runs its built-in demo drip (captions, noticed events, why tags)
  const WS = window.WebSocket;
  window.WebSocket = function (url, proto) {
    if (String(url).includes('/ws/session/')) return Object.assign(new EventTarget(), { readyState: 0, url: String(url), send() {}, close() {} });
    return new WS(url, proto);
  };
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  window.WebSocket.prototype = WS.prototype;
  md.getUserMedia = async () => {
    await wait(900);
    const ac = new AudioContext(); const d = ac.createMediaStreamDestination(); return d.stream;
  };
})();
"""

SMOOTH_SCROLL_JS = r"""
([sel, dy, ms]) => new Promise(res => {
  const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
  const from = el.scrollTop, to = Math.max(0, Math.min(el.scrollHeight - el.clientHeight, from + dy));
  const t0 = performance.now(); const ease = t => t < .5 ? 2*t*t : 1 - Math.pow(-2*t + 2, 2) / 2;
  const step = now => { const k = Math.min(1, (now - t0) / ms); el.scrollTop = from + (to - from) * ease(k); k < 1 ? requestAnimationFrame(step) : res(); };
  requestAnimationFrame(step);
})
"""


def _clean_list(route):
    """Drop unnamed scratch maps from the list (same rule as the app's isJunkWorkflow), so "latest map" is a real one."""
    if route.request.method != "GET":
        return route.continue_()
    resp = route.fetch()
    items = [w for w in resp.json() if len((w.get("name") or "").strip()) >= 4 and not w["workflow_id"].startswith("wf_test")]
    route.fulfill(response=resp, json=items)


class Rec:
    """One recorded browser context; remembers when the video started so the lead-in can be trimmed."""

    def __init__(self, pw, name: str, role: str, stub_media: bool = False):
        self.name = name
        self.dir = Path(tempfile.mkdtemp(prefix=f"claros-{name}-"))
        self.browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.ctx = self.browser.new_context(
            viewport={"width": W, "height": H},
            device_scale_factor=1,
            record_video_dir=str(self.dir),
            record_video_size={"width": W, "height": H},
        )
        self.ctx.add_init_script(INIT_JS.replace("__ROLE__", role))
        if stub_media:
            self.ctx.add_init_script(MEDIA_STUB_JS)
        self.ctx.set_default_timeout(20_000)
        self.ctx.route(f"{API}/api/workflows", _clean_list)
        self.page: Page = self.ctx.new_page()
        self.t0 = time.monotonic()
        self.marks: list[float] = []
        self.pos = (W * 0.62, H * 0.55)

    def mark(self):
        """Start of the usable footage."""
        self.marks.append(time.monotonic() - self.t0)

    def wait(self, ms: int):
        self.page.wait_for_timeout(ms)

    def park(self, x: float, y: float):
        self.pos = (x, y)
        self.page.mouse.move(x, y)

    def move(self, target, dx: float = 0, dy: float = 0, ms: int = 650):
        if isinstance(target, tuple):
            x, y = target
        else:
            box = target.bounding_box()
            assert box, f"no box for {target}"
            x, y = box["x"] + box["width"] / 2 + dx, box["y"] + box["height"] / 2 + dy
        x0, y0 = self.pos
        t0 = time.monotonic()
        while True:
            k = min(1.0, (time.monotonic() - t0) * 1000 / ms)
            e = k * k * (3 - 2 * k)  # smoothstep
            self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            if k >= 1:
                break
            time.sleep(0.012)
        self.pos = (x, y)

    def click(self, target, dx: float = 0, dy: float = 0, ms: int = 650, after: int = 900):
        self.move(target, dx, dy, ms)
        self.wait(140)
        self.page.mouse.down()
        self.wait(90)
        self.page.mouse.up()
        self.wait(after)

    def scroll(self, sel_or_handle, dy: int, ms: int = 1100):
        self.page.evaluate(SMOOTH_SCROLL_JS, [sel_or_handle, dy, ms])

    def t(self, label: str):
        print(f"    {time.monotonic() - self.t0:6.2f}s {label}", flush=True)

    def finish(self) -> Path:
        self.end = time.monotonic() - self.t0
        video = self.page.video
        self.ctx.close()
        self.browser.close()
        src = Path(video.path())
        return src


def ffmpeg(*args: str):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def encode(name: str, src: Path, start: float, dur: float):
    mp4, gif = OUT / f"{name}.mp4", OUT / f"{name}.gif"
    clip = src.with_suffix(".trim.mp4")
    # trim (frame-accurate re-encode) at native size
    ffmpeg("-ss", f"{start:.2f}", "-i", str(src), "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "slow", "-crf", "12", "-pix_fmt", "yuv420p", "-an", str(clip))
    ffmpeg("-i", str(clip), "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(mp4))
    pal = src.with_suffix(".pal.png")
    # mpdecimate drops near-identical frames (the GIF keeps their timing), so still moments cost ~nothing
    filt = f"fps={GIF_FPS},scale={GIF_W}:-1:flags=lanczos,mpdecimate=hi=64*24:lo=64*8:frac=0.1"
    for colors in (256, 192, 128, 96):
        ffmpeg("-i", str(clip), "-vf", f"{filt},palettegen=max_colors={colors}:stats_mode=diff", str(pal))
        ffmpeg("-i", str(clip), "-i", str(pal), "-lavfi", f"{filt}[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle", "-fps_mode", "vfr", str(gif))
        if gif.stat().st_size <= MAX_GIF:
            break
    for f in (mp4, gif):
        print(f"  {f.relative_to(OUT.parent.parent)}  {f.stat().st_size / 1e6:.2f} MB")


# ---------------------------------------------------------------- scenarios


def workmap(pw):
    r = Rec(pw, "workmap", "learner")
    p = r.page
    p.goto(f"{WEB}/map/{WORKFLOW}", wait_until="networkidle")
    p.get_by_role("button", name="Zoom screenshot").first.wait_for()
    r.park(980, 300)
    r.wait(400)
    r.mark()
    r.wait(500)
    step = lambda n: p.locator(f'button[aria-label^="Step {n}:"]')
    r.click(step(2), dx=-60, ms=550, after=900)
    r.click(step(3), dx=-60, ms=450, after=500)
    r.scroll("#step-list", 260, 600)
    r.click(step(5), dx=-60, ms=500, after=1000)  # "Experts differ"
    zoom = p.get_by_role("button", name="Zoom screenshot").last
    pane = zoom.locator("xpath=ancestor::div[contains(@class,'overflow-y-auto')][1]")
    r.move(pane, dy=80, ms=400)
    r.scroll(pane.element_handle(), 520, 1000)
    r.wait(800)
    r.scroll(pane.element_handle(), -520, 700)
    r.wait(150)
    r.click(zoom, ms=550, after=1100)
    close = p.locator("#claros-lightbox").get_by_role("button", name="Close").first
    r.click(close, ms=450, after=1000)
    src = r.finish()
    start = r.marks[0]
    encode("workmap", src, start, r.end - start - 0.15)


def learner(pw):
    r = Rec(pw, "learner", "learner")
    p = r.page
    # ?nudge=predict: the app's mock nudge driver builds a predict card from the loaded map (no mic / screen share)
    p.goto(f"{WEB}/?nudge=predict&judge=1", wait_until="networkidle")
    first = p.get_by_role("button").filter(has_text="Continue").first
    first.wait_for()
    r.wait(1200)
    r.park(760, 560)
    r.mark()
    r.wait(1100)
    r.click(first, dx=40, ms=750, after=2300)  # → feedback with the expert's reference
    # hard stop: change the query client-side (Next syncs useSearchParams with history.pushState)
    r.move((1000, 300), ms=400)
    p.evaluate("() => history.pushState(null, '', '/?nudge=stop&judge=1')")
    stop = p.locator("section").filter(has=p.get_by_role("button", name="Got it")).last
    stop.wait_for()
    r.wait(150)
    top = stop.evaluate("e => e.getBoundingClientRect().top + scrollY - 100")
    r.scroll("html", int(top - p.evaluate("scrollY")), 600)
    r.wait(1500)
    r.scroll("html", 380, 1000)
    r.wait(200)
    r.move(p.get_by_role("button", name="Got it").last, ms=650)
    r.wait(1000)
    src = r.finish()
    start = r.marks[0]
    encode("learner", src, start, r.end - start - 0.15)


def expert(pw):
    r = Rec(pw, "expert", "expert", stub_media=True)
    p = r.page
    # don't actually end the recorded session: an empty capture would be turned into a blank map server-side
    p.route(f"{API}/api/sessions/*/end", lambda route: route.fulfill(json={"ok": True}) if route.request.method == "POST" else route.continue_())
    p.goto(f"{WEB}/?judge=1", wait_until="networkidle")
    hero = p.get_by_role("button", name="Show Claros how you work")
    hero.wait_for()
    r.wait(600)
    r.park(1000, 640)
    r.mark()
    r.wait(500)
    r.click(hero, dx=40, dy=40, ms=700, after=0)
    r.t("hero clicked")
    for _ in range(100):
        print("   url", p.url, flush=True)
        if "/capture/" in p.url:
            break
        r.wait(200)
    sid = p.url.rstrip("/").split("/capture/")[1].split("?")[0]
    start_btn = p.get_by_role("button", name="Start", exact=True)
    start_btn.wait_for()
    r.t("start visible")
    r.wait(400)
    r.click(start_btn, ms=650, after=2700)  # share → mic → greet (active)
    r.t("start seq")
    seg1 = (r.marks[0], time.monotonic() - r.t0)
    # live capture in demo mode (same session)
    p.goto(f"{WEB}/capture/{sid}?demo=1&judge=1", wait_until="networkidle")
    p.get_by_role("button", name="Done — debrief").wait_for()
    r.mark()
    r.wait(3900)
    r.click(p.get_by_role("button", name="Done — debrief"), ms=800, after=0)
    r.t("done clicked")
    p.wait_for_url("**/debrief/**", wait_until="commit")
    r.t("debrief")
    r.wait(2300)
    src = r.finish()
    # two segments (skip the reload between them), joined with a short crossfade
    a = src.with_suffix(".a.mp4")
    b = src.with_suffix(".b.mp4")
    ffmpeg("-ss", f"{seg1[0]:.2f}", "-i", str(src), "-t", f"{seg1[1] - seg1[0]:.2f}", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", "-an", str(a))
    ffmpeg("-ss", f"{r.marks[1] + 0.2:.2f}", "-i", str(src), "-t", f"{r.end - r.marks[1] - 0.35:.2f}", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", "-an", str(b))
    da = seg1[1] - seg1[0]
    joined = src.with_suffix(".joined.mp4")
    ffmpeg("-i", str(a), "-i", str(b), "-filter_complex", f"[0:v]fps=25,settb=AVTB[x];[1:v]fps=25,settb=AVTB[y];[x][y]xfade=transition=fade:duration=0.35:offset={da - 0.4:.2f},format=yuv420p", "-c:v", "libx264", "-crf", "12", str(joined))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(joined)], capture_output=True, text=True).stdout)
    encode("expert", joined, 0, dur)


SCENARIOS = {"workmap": workmap, "learner": learner, "expert": expert}

if __name__ == "__main__":
    assert shutil.which("ffmpeg"), "ffmpeg not found"
    names = sys.argv[1:] or list(SCENARIOS)
    with sync_playwright() as pw:
        for n in names:
            print(f"recording {n} …", flush=True)
            SCENARIOS[n](pw)
