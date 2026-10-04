"use client";

// Dictionary i18n + UI prefs (language, role, theme). Dictionaries live in ./dict.ts.
import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import { de, en, es, fr, ru, type Dict, type DictKey } from "./dict";

export type { DictKey };
export const LANGS = ["en", "de", "fr", "es", "ru"] as const;
export type UiLang = (typeof LANGS)[number];
export const LANG_NAMES: Record<UiLang, string> = { en: "English", de: "Deutsch", fr: "Français", es: "Español", ru: "Русский" };

const DICTS: Record<UiLang, Dict> = { en, de, fr, es, ru };

export type Role = "learner" | "expert";
export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

type Ctx = {
  lang: UiLang;
  setLang: (l: UiLang) => void;
  role: Role;
  setRole: (r: Role) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
  t: (k: DictKey, vars?: Record<string, string | number>) => string;
};

const UiCtx = createContext<Ctx | null>(null);

function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = window.localStorage.getItem(key);
    return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, v: string) {
  try {
    window.localStorage.setItem(key, v);
  } catch {
    /* storage blocked: preference lives for this page only */
  }
}

export function translate(lang: UiLang, k: DictKey, vars?: Record<string, string | number>) {
  let s = DICTS[lang][k] ?? en[k] ?? k;
  if (vars) for (const [name, v] of Object.entries(vars)) s = s.replaceAll(`{${name}}`, String(v));
  return s;
}

// Tiny external store so prefs survive client navigation without flicker and hydrate safely.
const listeners = new Set<() => void>();
const prefs: { lang: UiLang; role: Role; theme: Theme; loaded: boolean } = { lang: "en", role: "learner", theme: "system", loaded: false };
function loadPrefs() {
  if (prefs.loaded || typeof window === "undefined") return;
  prefs.loaded = true;
  prefs.lang = read("claros.lang", LANGS, "en");
  prefs.role = read("claros.role", ["learner", "expert"] as const, "learner");
  prefs.theme = read("claros.theme", THEMES, "system");
}
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const emit = () => listeners.forEach((l) => l());
const getLang = () => (loadPrefs(), prefs.lang);
const getRole = () => (loadPrefs(), prefs.role);
const getTheme = () => (loadPrefs(), prefs.theme);

function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const c = document.documentElement.classList;
  c.toggle("dark", dark);
  c.toggle("light", !dark);
}

export function UiProvider({ children }: { children: React.ReactNode }) {
  const lang = useSyncExternalStore(subscribe, getLang, () => "en" as UiLang);
  const role = useSyncExternalStore(subscribe, getRole, () => "learner" as Role);
  const theme = useSyncExternalStore(subscribe, getTheme, () => "system" as Theme);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  useEffect(() => {
    applyTheme(theme);
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => applyTheme("system");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [theme]);

  const setLang = useCallback((l: UiLang) => {
    prefs.lang = l;
    write("claros.lang", l);
    emit();
  }, []);
  const setRole = useCallback((r: Role) => {
    prefs.role = r;
    write("claros.role", r);
    emit();
  }, []);
  const setTheme = useCallback((th: Theme) => {
    prefs.theme = th;
    write("claros.theme", th);
    emit();
  }, []);
  const t = useCallback((k: DictKey, vars?: Record<string, string | number>) => translate(lang, k, vars), [lang]);

  const value = useMemo(() => ({ lang, setLang, role, setRole, theme, setTheme, t }), [lang, setLang, role, setRole, theme, setTheme, t]);
  return <UiCtx.Provider value={value}>{children}</UiCtx.Provider>;
}

export function useUi() {
  const c = useContext(UiCtx);
  if (!c) throw new Error("useUi must be used inside <UiProvider>");
  return c;
}
export const useT = () => useUi().t;

export function timeAgo(t: (k: DictKey, v?: Record<string, string | number>) => string, ts: number) {
  if (ts < 1e12) ts *= 1000; // server sends epoch seconds
  const d = Math.max(0, Date.now() - ts) / 60_000;
  if (d < 1) return t("time.justNow");
  if (d < 60) return t("time.min", { n: Math.round(d) });
  if (d < 60 * 24) return t("time.hour", { n: Math.round(d / 60) });
  return t("time.day", { n: Math.round(d / 1440) });
}
