"use client";

import {
  AlertTriangle,
  BookOpen,
  Building2,
  ChevronLeft,
  ChevronRight,
  FileText,
  GraduationCap,
  LayoutDashboard,
  Library,
  LogOut,
  Menu,
  Settings,
  Shield,
  ShieldCheck,
  User,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { OrgSwitcher } from "@/components/layout/org-switcher";
import { NotificationCentre } from "@/features/notifications/components/notification-centre";
import { logout } from "@/features/auth/api";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/stores/session";
import { useUIStore } from "@/stores/ui";
import type { Role } from "@/types/auth";

type NavItem = {
  href: string;
  icon: typeof LayoutDashboard;
  label: string;
  roles?: Role[];
};

const navGroups: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Work",
    items: [
      { href: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
      { href: "/papers", icon: FileText, label: "Papers", roles: ["teacher", "coaching_admin"] },
      { href: "/question-bank", icon: Library, label: "Question Bank", roles: ["teacher", "coaching_admin", "super_admin"] },
      { href: "/textbooks", icon: BookOpen, label: "Textbooks", roles: ["teacher", "coaching_admin"] },
    ],
  },
  {
    label: "Administration",
    items: [
      { href: "/admin/programs", icon: GraduationCap, label: "Programs", roles: ["coaching_admin", "super_admin"] },
      { href: "/admin/approvals", icon: ShieldCheck, label: "Approvals", roles: ["coaching_admin", "super_admin"] },
      { href: "/admin/users", icon: Users, label: "Users", roles: ["coaching_admin"] },
      { href: "/admin/templates", icon: FileText, label: "Templates", roles: ["coaching_admin", "super_admin"] },
      { href: "/admin/billing", icon: Shield, label: "Billing", roles: ["coaching_admin"] },
    ],
  },
  {
    label: "Platform",
    items: [
      { href: "/platform/curriculum", icon: BookOpen, label: "Curriculum Ops", roles: ["super_admin"] },
      { href: "/platform/orgs", icon: Building2, label: "Organizations", roles: ["super_admin"] },
      { href: "/platform/audit", icon: Shield, label: "Audit", roles: ["super_admin"] },
    ],
  },
  {
    label: "Account",
    items: [
      { href: "/select-org", icon: Building2, label: "Select Org" },
      { href: "/settings", icon: Settings, label: "Settings" },
    ],
  },
];

const routeLabels: Record<string, string> = {
  "/admin": "Administration",
  "/admin/programs": "Programs",
  "/admin/approvals": "Approvals",
  "/admin/templates": "Templates",
  "/dashboard": "Dashboard",
  "/notifications": "Notifications",
  "/papers": "Papers",
  "/platform": "Platform",
  "/platform/curriculum": "Global Curriculum Ops",
  "/profile": "Profile",
  "/question-bank": "Question Bank",
  "/select-org": "Select Organization",
  "/settings": "Settings",
  "/textbooks": "Textbooks",
};

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const status = useSessionStore((state) => state.status);
  const bootstrapError = useSessionStore((state) => state.bootstrapError);
  const user = useSessionStore((state) => state.user);
  const role = useSessionStore((state) => state.role);
  const setUnauthenticated = useSessionStore((state) => state.setUnauthenticated);
  const sidebarCollapsed = useUIStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUIStore((state) => state.toggleSidebar);
  const title = titleForPath(pathname);
  const focusedBuilder = pathname === "/papers/new" || /^\/papers\/[^/]+\/builder$/.test(pathname);
  const compactSidebar = focusedBuilder || sidebarCollapsed;

  async function handleLogout() {
    try {
      await logout();
    } finally {
      setUnauthenticated();
      router.replace("/login");
    }
  }

  return (
    <div className="grid min-h-screen bg-background text-foreground md:grid-cols-[auto_1fr]">
      <aside
        className={cn(
          "hidden border-r border-border bg-card md:flex md:min-h-screen md:flex-col",
          compactSidebar ? "md:w-[72px]" : "md:w-[260px]",
        )}
      >
        <div className="flex h-16 items-center gap-3 border-b border-border px-4">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary text-sm font-semibold text-primary-foreground">
            PG
          </span>
          {!compactSidebar ? <span className="text-lg font-semibold">PaperGen</span> : null}
        </div>
        <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
          {status === "loading" ? <SidebarSkeleton collapsed={compactSidebar} /> : null}
          {status !== "loading"
            ? navGroups.map((group) => {
                const visibleItems = group.items.filter((item) => canShowItem(item, role));

                if (visibleItems.length === 0) {
                  return null;
                }

                return (
                  <div key={group.label}>
                    {!compactSidebar ? (
                      <p className="mb-2 px-2 text-xs font-semibold uppercase text-muted-foreground">{group.label}</p>
                    ) : null}
                    <div className="space-y-1">
                      {visibleItems.map((item) => {
                        const Icon = item.icon;
                        const active = isActive(pathname, item.href);

                        return (
                          <Link
                            className={cn(
                              "flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
                              active && "bg-primary/10 text-primary",
                              compactSidebar && "justify-center px-0",
                            )}
                            href={item.href}
                            key={item.href}
                            title={compactSidebar ? item.label : undefined}
                          >
                            <Icon className="h-4 w-4 shrink-0" />
                            {!compactSidebar ? <span className="truncate">{item.label}</span> : null}
                          </Link>
                        );
                      })}
                    </div>
                  </div>
                );
              })
            : null}
        </nav>
        <div className={cn("border-t border-border p-3", focusedBuilder && "hidden")}>
          <Button
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={cn("w-full", sidebarCollapsed && "px-0")}
            onClick={toggleSidebar}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            type="button"
            variant="ghost"
          >
            {sidebarCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            {!sidebarCollapsed ? "Collapse" : null}
          </Button>
        </div>
      </aside>
      <div className="flex min-h-screen min-w-0 flex-col">
        <header className={cn("sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur md:px-6", focusedBuilder && "hidden")}>
          <Button aria-label="Open navigation" className="md:hidden" size="icon" type="button" variant="ghost">
            <Menu className="h-5 w-5" />
          </Button>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>Home</span>
              <span>/</span>
              <span className="truncate">{title}</span>
            </div>
            <h1 className="truncate text-base font-semibold">{title}</h1>
          </div>
          <OrgSwitcher />
          <NotificationCentre />
          <div className="hidden min-w-0 items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm md:flex">
            <User className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="max-w-[150px] truncate">{user?.name ?? "Loading"}</span>
          </div>
          <Button aria-label="Sign out" onClick={handleLogout} size="icon" title="Sign out" type="button" variant="ghost">
            <LogOut className="h-5 w-5" />
          </Button>
        </header>
        <main className={cn("min-h-0 flex-1 overflow-y-auto", focusedBuilder ? "p-0" : "px-4 py-6 md:px-8")}>
          {status === "loading" ? (
            bootstrapError ? <SessionLoadError message={bootstrapError} /> : <ShellLoading />
          ) : children}
        </main>
        <footer className={cn("hidden border-t border-border px-6 py-3 text-xs text-muted-foreground md:block", focusedBuilder && "md:hidden")}>
          PaperGen workspace
        </footer>
      </div>
    </div>
  );
}

function canShowItem(item: NavItem, role: Role | null) {
  if (!item.roles) {
    return true;
  }

  return role ? item.roles.includes(role) : false;
}

function isActive(pathname: string, href: string) {
  return pathname === href || (href !== "/" && pathname.startsWith(`${href}/`));
}

function titleForPath(pathname: string) {
  const matched = Object.keys(routeLabels)
    .filter((route) => pathname === route || pathname.startsWith(`${route}/`))
    .sort((a, b) => b.length - a.length)[0];

  return matched ? routeLabels[matched] : "Workspace";
}

function SidebarSkeleton({ collapsed }: { collapsed: boolean }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: 6 }).map((_, index) => (
        <div className={cn("h-10 animate-pulse rounded-md bg-muted", collapsed ? "mx-auto w-10" : "w-full")} key={index} />
      ))}
    </div>
  );
}

function ShellLoading() {
  return (
    <div className="space-y-5">
      <div className="h-8 w-60 animate-pulse rounded-md bg-muted" />
      <div className="grid gap-4 md:grid-cols-3">
        <div className="h-28 animate-pulse rounded-lg bg-muted" />
        <div className="h-28 animate-pulse rounded-lg bg-muted" />
        <div className="h-28 animate-pulse rounded-lg bg-muted" />
      </div>
    </div>
  );
}

function SessionLoadError({ message }: { message: string }) {
  return (
    <div className="mx-auto mt-12 max-w-xl rounded-lg border border-amber-300 bg-amber-50 p-6 text-amber-950">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
        <div className="space-y-3">
          <div>
            <h2 className="font-semibold">PaperGen services are not ready</h2>
            <p className="mt-1 text-sm">{message} Retrying automatically…</p>
          </div>
          <Button onClick={() => window.location.reload()} type="button" variant="outline">
            Retry now
          </Button>
        </div>
      </div>
    </div>
  );
}
