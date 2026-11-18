import Link from "next/link";
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Cpu,
  Database,
  FileCheck2,
  FileDown,
  FileText,
  GraduationCap,
  Layers,
  LayoutDashboard,
  Library,
  LogIn,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function HomePage() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-50 selection:bg-primary selection:text-primary-foreground">
      {/* Glow Effects */}
      <div className="fixed top-0 left-1/2 -translate-x-1/2 w-full max-w-7xl h-96 bg-gradient-to-b from-blue-600/10 via-purple-600/10 to-transparent blur-3xl pointer-events-none" />

      {/* Top Navigation */}
      <header className="sticky top-0 z-50 border-b border-slate-800/80 bg-slate-950/80 backdrop-blur-md px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 font-bold text-white shadow-lg shadow-indigo-500/20">
              PG
            </span>
            <div>
              <span className="text-xl font-bold tracking-tight text-white">PaperGen</span>
              <span className="ml-2 hidden rounded-full bg-blue-500/10 px-2.5 py-0.5 text-[10px] font-semibold text-blue-400 border border-blue-500/20 sm:inline-block">
                AI Platform
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="hidden md:flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-400">
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
              All Systems Operational
            </div>

            <Button asChild size="sm" variant="ghost">
              <Link className="text-slate-300 hover:text-white" href="http://127.0.0.1:8000/api/docs" target="_blank">
                API Docs
              </Link>
            </Button>

            <Button asChild className="gap-1.5" size="sm" variant="outline">
              <Link href="/login">
                <LogIn className="h-4 w-4" />
                Sign In
              </Link>
            </Button>

            <Button asChild className="gap-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-md hover:from-blue-500 hover:to-indigo-500" size="sm">
              <Link href="/dashboard">
                <LayoutDashboard className="h-4 w-4" />
                Open Dashboard
              </Link>
            </Button>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <section className="relative px-6 pt-16 pb-12 text-center md:pt-24 md:pb-20">
        <div className="mx-auto max-w-4xl space-y-6">
          <div className="inline-flex items-center gap-2 rounded-full border border-indigo-500/30 bg-indigo-500/10 px-3.5 py-1 text-xs font-medium text-indigo-300">
            <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
            Gemini 2.5 Flash Solver • Bloom&apos;s Taxonomy Calibrated
          </div>

          <h1 className="text-4xl font-extrabold tracking-tight sm:text-6xl text-white">
            AI-Powered Question Paper Studio & Curriculum Engine
          </h1>

          <p className="mx-auto max-w-2xl text-lg text-slate-400 sm:text-xl font-normal leading-relaxed">
            Generate balanced CBSE & ICSE question papers with difficulty controls,
            chapter weight constraints, KaTeX formula formatting, and instant PDF/DOCX export.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-4 pt-4">
            <Button asChild className="gap-2 bg-blue-600 px-7 py-6 text-base font-semibold shadow-lg shadow-blue-500/25 hover:bg-blue-500" size="lg">
              <Link href="/dashboard">
                <LayoutDashboard className="h-5 w-5" />
                Launch Dashboard
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>

            <Button asChild className="gap-2 border-slate-700 bg-slate-900/60 px-7 py-6 text-base font-semibold text-slate-200 hover:bg-slate-800" size="lg" variant="outline">
              <Link href="/papers/new">
                <Sparkles className="h-5 w-5 text-indigo-400" />
                New Question Paper
              </Link>
            </Button>

            <Button asChild className="gap-2 border-slate-700 bg-slate-900/60 px-7 py-6 text-base font-semibold text-slate-200 hover:bg-slate-800" size="lg" variant="outline">
              <Link href="/question-bank">
                <Library className="h-5 w-5 text-purple-400" />
                Question Bank
              </Link>
            </Button>
          </div>
        </div>
      </section>

      {/* Feature Modules Matrix */}
      <section className="mx-auto max-w-7xl px-6 py-8">
        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-4">
          <Link className="group" href="/dashboard">
            <Card className="h-full border-slate-800 bg-slate-900/50 backdrop-blur transition-all duration-200 hover:-translate-y-1 hover:border-blue-500/50 hover:bg-slate-900/80 hover:shadow-xl hover:shadow-blue-500/5">
              <CardHeader className="pb-3">
                <div className="mb-2 inline-flex h-11 w-11 items-center justify-center rounded-lg bg-blue-500/10 text-blue-400 group-hover:bg-blue-500/20 group-hover:text-blue-300">
                  <LayoutDashboard className="h-5 w-5" />
                </div>
                <CardTitle className="text-lg font-semibold text-white">Full Dashboard</CardTitle>
                <CardDescription className="text-xs text-slate-400">
                  Live metrics, recent exam papers, approval pipelines, and system monitors.
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                <span className="inline-flex items-center gap-1 text-xs font-semibold text-blue-400 group-hover:gap-1.5 transition-all">
                  Open Workspace <ArrowRight className="h-3 w-3" />
                </span>
              </CardContent>
            </Card>
          </Link>

          <Link className="group" href="/papers/new">
            <Card className="h-full border-slate-800 bg-slate-900/50 backdrop-blur transition-all duration-200 hover:-translate-y-1 hover:border-indigo-500/50 hover:bg-slate-900/80 hover:shadow-xl hover:shadow-indigo-500/5">
              <CardHeader className="pb-3">
                <div className="mb-2 inline-flex h-11 w-11 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400 group-hover:bg-indigo-500/20 group-hover:text-indigo-300">
                  <Sparkles className="h-5 w-5" />
                </div>
                <CardTitle className="text-lg font-semibold text-white">AI Paper Studio</CardTitle>
                <CardDescription className="text-xs text-slate-400">
                  Interactive builder with drag-and-drop slots, question swap, and KaTeX math.
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                <span className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-400 group-hover:gap-1.5 transition-all">
                  Launch Studio <ArrowRight className="h-3 w-3" />
                </span>
              </CardContent>
            </Card>
          </Link>

          <Link className="group" href="/question-bank">
            <Card className="h-full border-slate-800 bg-slate-900/50 backdrop-blur transition-all duration-200 hover:-translate-y-1 hover:border-purple-500/50 hover:bg-slate-900/80 hover:shadow-xl hover:shadow-purple-500/5">
              <CardHeader className="pb-3">
                <div className="mb-2 inline-flex h-11 w-11 items-center justify-center rounded-lg bg-purple-500/10 text-purple-400 group-hover:bg-purple-500/20 group-hover:text-purple-300">
                  <Library className="h-5 w-5" />
                </div>
                <CardTitle className="text-lg font-semibold text-white">Question Bank</CardTitle>
                <CardDescription className="text-xs text-slate-400">
                  Curated repository of questions with chapter mappings, Bloom&apos;s level, and answers.
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                <span className="inline-flex items-center gap-1 text-xs font-semibold text-purple-400 group-hover:gap-1.5 transition-all">
                  Browse Questions <ArrowRight className="h-3 w-3" />
                </span>
              </CardContent>
            </Card>
          </Link>

          <Link className="group" href="/admin/templates">
            <Card className="h-full border-slate-800 bg-slate-900/50 backdrop-blur transition-all duration-200 hover:-translate-y-1 hover:border-amber-500/50 hover:bg-slate-900/80 hover:shadow-xl hover:shadow-amber-500/5">
              <CardHeader className="pb-3">
                <div className="mb-2 inline-flex h-11 w-11 items-center justify-center rounded-lg bg-amber-500/10 text-amber-400 group-hover:bg-amber-500/20 group-hover:text-amber-300">
                  <Layers className="h-5 w-5" />
                </div>
                <CardTitle className="text-lg font-semibold text-white">Blueprint Templates</CardTitle>
                <CardDescription className="text-xs text-slate-400">
                  Configure exam schemas: Section A to E, marks per slot, and internal choices.
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                <span className="inline-flex items-center gap-1 text-xs font-semibold text-amber-400 group-hover:gap-1.5 transition-all">
                  Manage Templates <ArrowRight className="h-3 w-3" />
                </span>
              </CardContent>
            </Card>
          </Link>
        </div>
      </section>

      {/* Demo Credentials & Quick Access Banner */}
      <section className="mx-auto max-w-7xl px-6 py-6">
        <div className="rounded-2xl border border-blue-500/30 bg-gradient-to-r from-blue-950/40 via-slate-900/80 to-purple-950/40 p-6 md:p-8 backdrop-blur">
          <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <div className="space-y-2">
              <div className="inline-flex items-center gap-2 text-xs font-semibold text-blue-400 uppercase tracking-wider">
                <Zap className="h-3.5 w-3.5" />
                Quick Access Demo Accounts
              </div>
              <h3 className="text-xl font-bold text-white">Ready-to-Use Local Credentials</h3>
              <p className="text-xs text-slate-400 max-w-xl">
                Use the pre-seeded accounts to explore with full admin or teacher privileges.
                Clicking sign in will allow you to sign in with one click.
              </p>
              <div className="flex flex-wrap gap-4 pt-2 text-xs text-slate-300 font-mono">
                <div className="rounded-lg bg-slate-950/70 border border-slate-800 px-3 py-1.5">
                  <span className="text-slate-500">Admin: </span>
                  <span className="text-blue-300 font-semibold">admin@papergen.local</span>
                  <span className="text-slate-500"> | Pwd: </span>
                  <span className="text-emerald-300 font-semibold">Papergen123!</span>
                </div>
                <div className="rounded-lg bg-slate-950/70 border border-slate-800 px-3 py-1.5">
                  <span className="text-slate-500">Teacher: </span>
                  <span className="text-purple-300 font-semibold">teacher@papergen.local</span>
                  <span className="text-slate-500"> | Pwd: </span>
                  <span className="text-emerald-300 font-semibold">Papergen123!</span>
                </div>
              </div>
            </div>
            <div className="flex flex-wrap gap-3">
              <Button asChild className="gap-2 bg-blue-600 hover:bg-blue-500 text-white font-semibold">
                <Link href="/login">
                  <LogIn className="h-4 w-4" />
                  Sign In with Demo Account
                </Link>
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* Architecture & Live Verification Bar */}
      <footer className="border-t border-slate-900 bg-slate-950 px-6 py-8 mt-12 text-xs text-slate-500">
        <div className="mx-auto max-w-7xl flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-4 flex-wrap">
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className="h-2 w-2 rounded-full bg-emerald-400" />
              FastAPI 0.1.0 (Port 8000)
            </span>
            <span>•</span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className="h-2 w-2 rounded-full bg-emerald-400" />
              Next.js 15 App Router (Port 3000)
            </span>
            <span>•</span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className="h-2 w-2 rounded-full bg-emerald-400" />
              PostgreSQL & Alembic (Rev 0040)
            </span>
            <span>•</span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className="h-2 w-2 rounded-full bg-emerald-400" />
              Redis Cache & Queue (Port 6379)
            </span>
          </div>
          <div>
            PaperGen AI Question Paper Generation System
          </div>
        </div>
      </footer>
    </div>
  );
}
