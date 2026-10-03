"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Languages } from "lucide-react";
import { cn } from "@/lib/utils";
import "./claros.css";
import { LANGS, LANG_NAMES, UiProvider, useUi, type Role, type UiLang } from "./i18n";
import { ClarosDot } from "./primitives";

export function Shell({ children, wide, bare }: { children: React.ReactNode; wide?: boolean; bare?: boolean }) {
  return (
    <UiProvider>
      <div className="claros-root flex flex-col">
        <TopBar />
        <main id="main" className={cn("mx-auto w-full flex-1 px-4 pb-16 sm:px-6", bare ? "" : "pt-6 sm:pt-8", wide ? "max-w-[1400px]" : "max-w-[1120px]")}>
          {children}
        </main>
      </div>
    </UiProvider>
  );
}

const NAV = [
  { href: "/", key: "nav.home" as const },
  { href: "/learn", key: "nav.learn" as const },
  { href: "/inbox", key: "nav.inbox" as const },
  { href: "/map/wf_ap_purchase_invoice", key: "nav.map" as const, match: "/map" },
];

function TopBar() {
  const { t, lang, setLang } = useUi();
  const path = usePathname() || "/";
  return (
    <header className="sticky top-0 z-30 border-b-2 border-[var(--ink)] bg-[var(--paper)]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:bg-white focus:p-2">
        {t("nav.skip")}
      </a>
      <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 rounded-[4px] pr-1" aria-label="Claros home">
          <ClarosDot size={22} />
          <span className="text-xl font-black tracking-[-0.04em]">claros</span>
        </Link>
        <nav aria-label="Primary" className="ml-2 hidden items-center gap-1 md:flex">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" : path.startsWith(n.match ?? n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-[4px] border-2 px-2.5 py-1 text-sm font-bold transition-colors",
                  active ? "border-[var(--ink)] bg-white shadow-[2px_2px_0_0_var(--ink)]" : "border-transparent hover:border-[var(--ink)]",
                )}
              >
                {t(n.key)}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <RoleSwitch />
          <label className="relative inline-flex items-center">
            <span className="sr-only">{t("lang.label")}</span>
            <Languages className="pointer-events-none absolute left-2 size-4" aria-hidden />
            <select
              value={lang}
              onChange={(e) => setLang(e.target.value as UiLang)}
              className="h-9 appearance-none rounded-[4px] border-2 border-[var(--ink)] bg-white pl-7 pr-2 text-sm font-bold uppercase"
            >
              {LANGS.map((l) => (
                <option key={l} value={l}>
                  {l.toUpperCase()} · {LANG_NAMES[l]}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <nav aria-label="Primary mobile" className="flex gap-1 overflow-x-auto border-t-2 border-[var(--ink)] px-4 py-1.5 md:hidden">
        {NAV.map((n) => {
          const active = n.href === "/" ? path === "/" : path.startsWith(n.match ?? n.href);
          return (
            <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined} className={cn("shrink-0 rounded-[4px] border-2 px-2 py-0.5 text-sm font-bold", active ? "border-[var(--ink)] bg-white" : "border-transparent")}>
              {t(n.key)}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}

export function RoleSwitch({ large }: { large?: boolean }) {
  const { t, role, setRole } = useUi();
  const opts: Role[] = ["learner", "expert"];
  return (
    <div role="radiogroup" aria-label={t("role.label")} className={cn("inline-flex rounded-[5px] border-2 border-[var(--ink)] bg-white p-0.5", large && "p-1")}>
      {opts.map((r) => (
        <button
          key={r}
          type="button"
          role="radio"
          aria-checked={role === r}
          onClick={() => setRole(r)}
          className={cn(
            "rounded-[3px] font-bold transition-colors",
            large ? "px-4 py-2 text-base" : "px-2.5 py-1 text-xs sm:text-sm",
            role === r ? "bg-[var(--ink)] text-[var(--paper)]" : "hover:bg-[var(--paper-2)]",
          )}
        >
          {t(r === "learner" ? "role.learner" : "role.expert")}
        </button>
      ))}
    </div>
  );
}
