"use client";
/**
 * Document Picture-in-Picture helpers. The PiP window only renders UI (portal);
 * the ElevenLabs session, mic and capture all stay in the opener tab.
 */
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

interface DocumentPictureInPicture {
  requestWindow(opts?: { width?: number; height?: number; disallowReturnToOpener?: boolean; preferInitialWindowPlacement?: boolean }): Promise<Window>;
  window: Window | null;
}

function dpip(): DocumentPictureInPicture | undefined {
  if (typeof window === "undefined") return undefined;
  return (window as unknown as { documentPictureInPicture?: DocumentPictureInPicture }).documentPictureInPicture;
}

export function pipSupported() {
  return !!dpip();
}

function copyStyles(target: Document) {
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const css = Array.from(sheet.cssRules).map((r) => r.cssText).join("\n");
      const style = target.createElement("style");
      style.textContent = css;
      target.head.appendChild(style);
    } catch {
      if (sheet.href) {
        const link = target.createElement("link");
        link.rel = "stylesheet";
        link.href = sheet.href;
        target.head.appendChild(link);
      }
    }
  }
  target.documentElement.className = document.documentElement.className;
  target.body.className = document.body.className;
  target.body.style.margin = "0";
}

export function usePip() {
  const [pipWindow, setPipWindow] = useState<Window | null>(null);
  const [supported, setSupported] = useState(false);
  useEffect(() => setSupported(pipSupported()), []);

  /** Must be called from a user gesture (click). */
  const open = useCallback(async (size: { width?: number; height?: number } = { width: 360, height: 230 }) => {
    const api = dpip();
    if (!api) return null;
    if (api.window) {
      setPipWindow(api.window);
      return api.window;
    }
    const w = await api.requestWindow({ ...size });
    copyStyles(w.document);
    w.document.title = "Claros";
    w.addEventListener("pagehide", () => setPipWindow(null), { once: true });
    setPipWindow(w);
    return w;
  }, []);

  const close = useCallback(() => {
    pipWindow?.close();
    setPipWindow(null);
  }, [pipWindow]);

  return { supported, pipWindow, open, close };
}

export function PipPortal({ pipWindow, children }: { pipWindow: Window | null; children: React.ReactNode }) {
  if (!pipWindow) return null;
  return createPortal(<div className="p-2 min-h-screen bg-background">{children}</div>, pipWindow.document.body);
}
