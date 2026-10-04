"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, Check, ChevronDown, Home, Languages, LibraryBig, LogOut, Menu } from "lucide-react";
import { cn } from "@/lib/utils";
import { EXPERT, LEA } from "@/lib/mock";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { NavigationMenu, NavigationMenuItem, NavigationMenuLink, NavigationMenuList } from "@/components/ui/navigation-menu";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { LANGS, LANG_NAMES, UiProvider, useUi, type DictKey, type Role, type UiLang } from "./i18n";
import { ClarosDot, ExpertAvatar } from "./primitives";
import { DebugPanel } from "./debug";
import { LightboxProvider } from "./Lightbox";

/** Wide, fluid container shared by every surface (top bar, pages): fills the screen up to 1600px. */
export const CONTAINER = "mx-auto w-full max-w-[1600px] px-4 sm:px-6 lg:px-10";

export function Shell({
  children,
  back,
  focus,
}: {
  children: React.ReactNode;
  /** inner pages: one back link under the top bar (may depend on the role) */
  back?: Back | ((role: Role) => Back);
  /** focused mode (capture, debrief, live session): minimal chrome, no nav */
  focus?: { label: DictKey; detail?: React.ReactNode };
}) {
  return (
    <UiProvider>
      <LightboxProvider>
        <div className="flex min-h-dvh flex-col bg-paper text-ink">
          {focus ? <FocusBar label={focus.label} detail={focus.detail} /> : <TopBar />}
          <main id="main" className={cn(CONTAINER, "flex-1 pb-24")}>
            {back ? <BackLink back={back} /> : <div className="h-8 sm:h-12" />}
            {children}
          </main>
          <DebugPanel />
        </div>
      </LightboxProvider>
    </UiProvider>
  );
}

type NavItem = { href: string; key: DictKey; icon: typeof Home; match: (p: string) => boolean };
const NAV: Record<Role, NavItem[]> = {
  expert: [
    { href: "/", key: "nav.home", icon: Home, match: (p) => p === "/" },
    { href: "/map", key: "nav.maps", icon: LibraryBig, match: (p) => p.startsWith("/map") },
  ],
  // learners live on Home (Start + their status); no other destinations
  learner: [],
};

type Back = { href: string; label: DictKey };
function BackLink({ back }: { back: Back | ((role: Role) => Back) }) {
  const { t, role } = useUi();
  const { href, label } = typeof back === "function" ? back(role) : back;
  return (
    <div className="py-5 sm:py-6">
      <Link href={href} className="inline-flex items-center gap-1.5 rounded-[4px] text-base font-bold text-ink-2 underline decoration-transparent decoration-2 underline-offset-4 transition-colors hover:text-ink hover:decoration-ink">
        <ArrowLeft className="size-5" aria-hidden /> {t(label)}
      </Link>
    </div>
  );
}

function Wordmark() {
  return (
    <Link href="/" className="group flex shrink-0 items-center gap-2 rounded-[4px] pr-1" aria-label="Claros — home">
      <ClarosDot size={28} className="transition-transform duration-200 group-hover:rotate-12" />
      <span className="text-[26px] font-black leading-none tracking-[-0.05em]">claros</span>
    </Link>
  );
}

function SkipLink() {
  const { t } = useUi();
  return (
    <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-[4px] focus:border-2 focus:border-ink focus:bg-card focus:p-2">
      {t("nav.skip")}
    </a>
  );
}

function TopBar() {
  const { t, role } = useUi();
  const path = usePathname() || "/";
  const [menu, setMenu] = useState(false);
  const nav = NAV[role];
  return (
    <header className="sticky top-0 z-30 border-b-2 border-ink bg-paper/95 backdrop-blur-[2px]">
      <SkipLink />
      <div className={cn(CONTAINER, "flex h-[72px] items-center gap-2 sm:gap-4")}>
        {nav.length ? <Button variant="ghost" size="icon-sm" className="md:hidden" aria-label={t("nav.menu")} onClick={() => setMenu(true)}>
          <Menu />
        </Button> : null}
        <Wordmark />
        {nav.length ? <NavigationMenu aria-label={t("nav.primary")} className="ml-4 hidden max-w-none flex-none border-0 bg-transparent p-0 md:flex">
          <NavigationMenuList className="space-x-2">
            {nav.map((n) => {
              const active = n.match(path);
              return (
                <NavigationMenuItem key={n.href}>
                  <NavigationMenuLink
                    active={active}
                    render={<Link href={n.href} aria-current={active ? "page" : undefined} />}
                    className={cn(
                      "flex h-11 items-center gap-2 rounded-base border-2 px-3.5 text-base font-bold leading-none",
                      active
                        ? "border-ink bg-ink text-paper hover:bg-ink hover:text-paper focus:bg-ink focus:text-paper"
                        : "press press-sm border-ink bg-card text-ink shadow-hard-sm hover:bg-card hover:text-ink focus:bg-card focus:text-ink",
                    )}
                  >
                    <n.icon className="size-4" aria-hidden />
                    {t(n.key)}
                  </NavigationMenuLink>
                </NavigationMenuItem>
              );
            })}
          </NavigationMenuList>
        </NavigationMenu> : null}
        <div className="ml-auto flex items-center gap-2">
          <LangMenu />
          <RoleMenu />
        </div>
      </div>
      <MobileNav open={menu} onOpenChange={setMenu} path={path} nav={nav} />
    </header>
  );
}

function MobileNav({ open, onOpenChange, path, nav }: { open: boolean; onOpenChange: (o: boolean) => void; path: string; nav: NavItem[] }) {
  const { t } = useUi();
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" className="w-[86vw] max-w-sm gap-0 border-0 border-r-2 border-ink bg-paper p-0 text-ink">
        <SheetHeader className="border-b-2 border-ink p-4">
          <SheetTitle className="flex items-center gap-2 text-xl font-black tracking-[-0.04em]">
            <ClarosDot size={22} /> claros
          </SheetTitle>
        </SheetHeader>
        <nav aria-label={t("nav.primary")} className="flex flex-col gap-2 p-4">
          {nav.map((n) => {
            const active = n.match(path);
            return (
              <Link
                key={n.href}
                href={n.href}
                onClick={() => onOpenChange(false)}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-14 items-center gap-3 rounded-base border-2 border-ink px-4 text-lg font-extrabold",
                  active ? "bg-ink text-paper" : "press press-sm bg-card shadow-hard-sm",
                )}
              >
                <n.icon className="size-5" aria-hidden />
                {t(n.key)}
              </Link>
            );
          })}
        </nav>
      </SheetContent>
    </Sheet>
  );
}

function FocusBar({ label, detail }: { label: DictKey; detail?: React.ReactNode }) {
  const { t } = useUi();
  return (
    <header className="sticky top-0 z-30 border-b-2 border-ink bg-paper">
      <SkipLink />
      <div className={cn(CONTAINER, "flex h-16 items-center gap-3")}>
        <Wordmark />
        <span aria-hidden className="h-6 w-0.5 bg-ink" />
        <p className="min-w-0 truncate text-base font-extrabold">
          {t(label)}
          {detail ? <span className="ml-2 text-sm font-semibold text-ink-2">{detail}</span> : null}
        </p>
        <div className="ml-auto flex items-center gap-2">
          <LangMenu />
          <Link href="/" className={buttonVariants({ variant: "ghost", size: "sm" })}>
            <LogOut aria-hidden /> <span className="hidden sm:inline">{t("focus.exit")}</span>
          </Link>
        </div>
      </div>
    </header>
  );
}

function LangMenu() {
  const { t, lang, setLang } = useUi();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="outline" size="sm" aria-label={`${t("lang.label")}: ${LANG_NAMES[lang]}`} className="gap-1.5 px-3" />}
      >
        <Languages aria-hidden />
        <span className="font-mono uppercase">{lang}</span>
        <ChevronDown className="size-3.5! opacity-70" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuRadioGroup value={lang} onValueChange={(v) => setLang(v as UiLang)}>
          <DropdownMenuLabel className="text-xs text-ink-2">{t("lang.label")}</DropdownMenuLabel>
          {LANGS.map((l) => (
            <DropdownMenuRadioItem key={l} value={l} lang={l}>
              <span className="w-6 font-mono text-xs uppercase opacity-70">{l}</span>
              {LANG_NAMES[l]}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const PERSONA: Record<Role, { user: typeof LEA; sub: DictKey; label: DictKey }> = {
  learner: { user: LEA, sub: "role.learner.sub", label: "role.learner" },
  expert: { user: EXPERT, sub: "role.expert.sub", label: "role.expert" },
};

function RoleMenu() {
  const { t, role, setRole } = useUi();
  const me = PERSONA[role];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="outline"
            aria-label={`${t("role.switch")}: ${t(me.label)}`}
            className="h-11 gap-2 pl-1 pr-2.5"
          />
        }
      >
        <ExpertAvatar user={me.user} size={30} index={role === "expert" ? 0 : 2} />
        <span className="hidden flex-col items-start leading-none lg:flex">
          <span className="text-sm font-extrabold">{me.user.name}</span>
          <span className="mt-0.5 text-xs font-semibold opacity-75">{t(me.label)}</span>
        </span>
        <ChevronDown className="size-3.5! opacity-70" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuRadioGroup value={role} onValueChange={(v) => setRole(v as Role)}>
          <DropdownMenuLabel className="text-xs text-ink-2">{t("role.label")}</DropdownMenuLabel>
          {(["expert", "learner"] as Role[]).map((r) => {
            const p = PERSONA[r];
            return (
              <DropdownMenuRadioItem key={r} value={r} className="py-2 pl-2 [&>span:first-child]:hidden">
                <ExpertAvatar user={p.user} size={30} index={r === "expert" ? 0 : 2} />
                <span className="flex min-w-0 flex-1 flex-col leading-tight">
                  <span className="font-extrabold">
                    {t(p.label)} <span className="font-semibold opacity-75">· {p.user.name}</span>
                  </span>
                  <span className="text-xs opacity-80">{t(p.sub)}</span>
                </span>
                {role === r ? <Check className="ml-1" aria-hidden /> : null}
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
