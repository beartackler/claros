"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment, useState } from "react";
import { Check, ChevronDown, Home, Languages, LibraryBig, LogOut, Menu, Monitor, Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { EXPERT, LEA } from "@/lib/mock";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { NavigationMenu, NavigationMenuItem, NavigationMenuLink, NavigationMenuList } from "@/components/ui/navigation-menu";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { LANGS, LANG_NAMES, THEMES, UiProvider, useUi, type DictKey, type Role, type Theme, type UiLang } from "./i18n";
import { ClarosDot, ExpertAvatar } from "./primitives";
import { DebugPanel } from "./debug";

export type Crumb = { key?: DictKey; label?: string; href?: string };

export function Shell({
  children,
  wide,
  crumbs,
  focus,
}: {
  children: React.ReactNode;
  wide?: boolean;
  /** inner pages: Home › … trail under the top bar */
  crumbs?: Crumb[];
  /** focused mode (capture, debrief): minimal chrome, no nav */
  focus?: { label: DictKey; detail?: React.ReactNode };
}) {
  return (
    <UiProvider>
      <div className="flex min-h-dvh flex-col bg-paper text-ink">
        {focus ? <FocusBar label={focus.label} detail={focus.detail} /> : <TopBar />}
        <main id="main" className={cn("mx-auto w-full flex-1 px-4 pb-20 sm:px-6", wide ? "max-w-[1400px]" : "max-w-[1200px]")}>
          {crumbs?.length ? <Crumbs items={crumbs} /> : <div className="h-6 sm:h-8" />}
          {children}
        </main>
        <DebugPanel />
      </div>
    </UiProvider>
  );
}

const NAV: { href: string; key: DictKey; icon: typeof Home; match: (p: string) => boolean }[] = [
  { href: "/", key: "nav.home", icon: Home, match: (p) => p === "/" },
  { href: "/learn", key: "nav.learn", icon: Monitor, match: (p) => p.startsWith("/learn") },
  { href: "/map", key: "nav.workflows", icon: LibraryBig, match: (p) => p.startsWith("/map") },
];

function Wordmark() {
  return (
    <Link href="/" className="group flex shrink-0 items-center gap-2 rounded-[4px] pr-1" aria-label="Claros — home">
      <ClarosDot size={24} className="transition-transform duration-200 group-hover:rotate-12" />
      <span className="text-[22px] font-black leading-none tracking-[-0.05em]">claros</span>
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
  const { t } = useUi();
  const path = usePathname() || "/";
  const [menu, setMenu] = useState(false);
  return (
    <header className="sticky top-0 z-30 border-b-2 border-ink bg-paper/95 backdrop-blur-[2px]">
      <SkipLink />
      <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-2 px-4 sm:gap-4 sm:px-6">
        <Button variant="ghost" size="icon-sm" className="md:hidden" aria-label={t("nav.menu")} onClick={() => setMenu(true)}>
          <Menu />
        </Button>
        <Wordmark />
        <NavigationMenu aria-label={t("nav.primary")} className="ml-4 hidden max-w-none flex-none border-0 bg-transparent p-0 md:flex">
          <NavigationMenuList className="space-x-1">
            {NAV.map((n) => {
              const active = n.match(path);
              return (
                <NavigationMenuItem key={n.href}>
                  <NavigationMenuLink
                    active={active}
                    render={<Link href={n.href} aria-current={active ? "page" : undefined} />}
                    className={cn(
                      "flex h-10 items-center gap-2 rounded-base border-2 px-3 text-sm font-bold leading-none transition-[background-color,box-shadow,transform] duration-150",
                      active
                        ? "border-ink bg-ink text-paper shadow-[3px_3px_0_0_var(--claros)] hover:bg-ink hover:text-paper focus:bg-ink focus:text-paper"
                        : "border-transparent hover:border-ink hover:bg-card hover:text-ink focus:border-ink focus:bg-card focus:text-ink",
                    )}
                  >
                    <n.icon className="size-4" aria-hidden />
                    {t(n.key)}
                  </NavigationMenuLink>
                </NavigationMenuItem>
              );
            })}
          </NavigationMenuList>
        </NavigationMenu>
        <div className="ml-auto flex items-center gap-2">
          <LangMenu />
          <RoleMenu />
        </div>
      </div>
      <MobileNav open={menu} onOpenChange={setMenu} path={path} />
    </header>
  );
}

function MobileNav({ open, onOpenChange, path }: { open: boolean; onOpenChange: (o: boolean) => void; path: string }) {
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
          {NAV.map((n) => {
            const active = n.match(path);
            return (
              <Link
                key={n.href}
                href={n.href}
                onClick={() => onOpenChange(false)}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-14 items-center gap-3 rounded-base border-2 border-ink px-4 text-lg font-extrabold",
                  active ? "bg-ink text-paper shadow-[3px_3px_0_0_var(--claros)]" : "bg-card shadow-hard-sm active:translate-y-px",
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
      <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-4 sm:px-6">
        <Wordmark />
        <span aria-hidden className="h-6 w-0.5 bg-ink" />
        <p className="min-w-0 truncate text-sm font-extrabold">
          {t(label)}
          {detail ? <span className="ml-2 font-mono text-xs font-medium text-ink-2">{detail}</span> : null}
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

function Crumbs({ items }: { items: Crumb[] }) {
  const { t } = useUi();
  const all: Crumb[] = [{ key: "crumb.home", href: "/" }, ...items];
  return (
    <Breadcrumb className="py-4 sm:py-5">
      <BreadcrumbList className="gap-1 text-sm sm:gap-1.5">
        {all.map((c, i) => {
          const label = c.label ?? (c.key ? t(c.key) : "");
          const last = i === all.length - 1;
          return (
            <Fragment key={i}>
              <BreadcrumbItem className="min-w-0">
                {last || !c.href ? (
                  <BreadcrumbPage className="truncate font-bold">{label}</BreadcrumbPage>
                ) : (
                  <BreadcrumbLink
                    render={<Link href={c.href} />}
                    className="rounded-[3px] font-semibold text-ink-2 underline decoration-transparent decoration-2 underline-offset-4 transition-colors hover:text-ink hover:decoration-ink"
                  >
                    {label}
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
              {!last ? <BreadcrumbSeparator className="text-ink-2" /> : null}
            </Fragment>
          );
        })}
      </BreadcrumbList>
    </Breadcrumb>
  );
}

function LangMenu() {
  const { t, lang, setLang } = useUi();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="outline" size="sm" aria-label={`${t("lang.label")}: ${LANG_NAMES[lang]}`} className="gap-1.5 px-2.5 data-popup-open:bg-ink data-popup-open:text-paper" />}
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
const THEME_ICON: Record<Theme, typeof Sun> = { system: Monitor, light: Sun, dark: Moon };

function RoleMenu() {
  const { t, role, setRole, theme, setTheme } = useUi();
  const me = PERSONA[role];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="outline"
            aria-label={`${t("role.switch")}: ${t(me.label)}`}
            className="h-10 gap-2 pl-1 pr-2 data-popup-open:bg-ink data-popup-open:text-paper sm:pr-2.5"
          />
        }
      >
        <ExpertAvatar user={me.user} size={30} index={role === "expert" ? 0 : 2} />
        <span className="hidden flex-col items-start leading-none lg:flex">
          <span className="text-[13px] font-extrabold">{me.user.name}</span>
          <span className="mt-0.5 text-[11px] font-semibold opacity-75">{t(me.label)}</span>
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
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => setTheme(v as Theme)}>
          <DropdownMenuLabel className="text-xs text-ink-2">{t("theme.label")}</DropdownMenuLabel>
          {THEMES.map((th) => {
            const Icon = THEME_ICON[th];
            return (
              <DropdownMenuRadioItem key={th} value={th}>
                <Icon aria-hidden /> {t(`theme.${th}` as DictKey)}
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
