"use client";

import Link from "next/link";
import {
  Activity,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Clock,
  Cpu,
  Database,
  FileCheck2,
  FileDown,
  FileText,
  GraduationCap,
  Layers,
  Library,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useSessionStore } from "@/stores/session";

const RECENT_PAPERS = [
  {
    id: "paper-cbse-sci-10-midterm",
    title: "Class 10 Science Mid-Term Examination 2025-26",
    board: "CBSE",
    grade: "Class 10",
    subject: "Science",
    marks: 80,
    sections: 5,
    questionsCount: 39,
    status: "approved",
    updatedAt: "Today, 14:30",
  },
  {
    id: "paper-cbse-math-10-preboard",
    title: "Mathematics Standard Pre-Board Comprehensive",
    board: "CBSE",
    grade: "Class 10",
    subject: "Mathematics",
    marks: 80,
    sections: 5,
    questionsCount: 38,
    status: "draft",
    updatedAt: "Yesterday, 18:45",
  },
  {
    id: "paper-chem-rxn-unit",
    title: "Unit Assessment: Chemical Reactions & Equations",
    board: "CBSE",
    grade: "Class 10",
    subject: "Science (Chemistry)",
    marks: 25,
    sections: 3,
    questionsCount: 15,
    status: "approved",
    updatedAt: "Sep 28, 2026",
  },
  {
    id: "paper-acids-bases-quiz",
    title: "Formative Quiz: Acids, Bases and Salts",
    board: "CBSE",
    grade: "Class 10",
    subject: "Science (Chemistry)",
    marks: 20,
    sections: 2,
    questionsCount: 10,
    status: "review",
    updatedAt: "Sep 27, 2026",
  },
];

export default function DashboardPage() {
  const user = useSessionStore((state) => state.user);
  const role = useSessionStore((state) => state.role);

  return (
    <div className="mx-auto max-w-[1500px] space-y-8">
      {/* Header Banner */}
      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-3xl font-bold tracking-tight">PaperGen Workspace</h1>
            <Badge className="bg-emerald-500/10 text-emerald-600 border-emerald-500/20 text-xs">
              AI Engine Active
            </Badge>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Welcome back, <span className="font-semibold text-foreground">{user?.name ?? "Educator"}</span>. 
            Here is your live question paper generation pipeline and curriculum overview.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <Button asChild className="gap-2 shadow-sm" variant="default">
            <Link href="/papers/new">
              <Sparkles className="h-4 w-4" />
              Generate Question Paper
            </Link>
          </Button>
          <Button asChild className="gap-2" variant="outline">
            <Link href="/question-bank">
              <Library className="h-4 w-4" />
              Question Bank
            </Link>
          </Button>
        </div>
      </div>

      {/* Metric Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="relative overflow-hidden border-border bg-card/60 backdrop-blur-sm shadow-sm hover:shadow transition-shadow">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Papers Generated
            </CardTitle>
            <div className="rounded-lg bg-blue-500/10 p-2 text-blue-500">
              <FileText className="h-4 w-4" />
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">48</div>
            <div className="mt-1 flex items-center text-xs text-emerald-500 font-medium">
              <TrendingUp className="mr-1 h-3.5 w-3.5" />
              +14% from last month
            </div>
          </CardContent>
        </Card>

        <Card className="relative overflow-hidden border-border bg-card/60 backdrop-blur-sm shadow-sm hover:shadow transition-shadow">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Question Inventory
            </CardTitle>
            <div className="rounded-lg bg-purple-500/10 p-2 text-purple-500">
              <Library className="h-4 w-4" />
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">1,480</div>
            <div className="mt-1 flex items-center text-xs text-muted-foreground">
              CBSE Class 10 (Science, Math)
            </div>
          </CardContent>
        </Card>

        <Card className="relative overflow-hidden border-border bg-card/60 backdrop-blur-sm shadow-sm hover:shadow transition-shadow">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Active Blueprints
            </CardTitle>
            <div className="rounded-lg bg-amber-500/10 p-2 text-amber-500">
              <Layers className="h-4 w-4" />
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">12</div>
            <div className="mt-1 flex items-center text-xs text-muted-foreground">
              Pre-Board, Unit Tests, Annual
            </div>
          </CardContent>
        </Card>

        <Card className="relative overflow-hidden border-border bg-card/60 backdrop-blur-sm shadow-sm hover:shadow transition-shadow">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              AI Solver Accuracy
            </CardTitle>
            <div className="rounded-lg bg-emerald-500/10 p-2 text-emerald-500">
              <ShieldCheck className="h-4 w-4" />
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">99.8%</div>
            <div className="mt-1 flex items-center text-xs text-emerald-500 font-medium">
              Blueprint Constraints Verified
            </div>
          </CardContent>
        </Card>
      </div>

      {/* AI Studio Hero Banner */}
      <Card className="relative overflow-hidden border-primary/20 bg-gradient-to-r from-blue-950/40 via-indigo-950/30 to-purple-950/40 shadow-md">
        <div className="absolute right-0 top-0 -mr-16 -mt-16 h-64 w-64 rounded-full bg-primary/10 blur-3xl" />
        <CardContent className="relative p-6 sm:p-8">
          <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <div className="space-y-2 max-w-2xl">
              <div className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                <Sparkles className="h-3.5 w-3.5" />
                Next-Gen AI Question Paper Generation
              </div>
              <h2 className="text-2xl font-bold tracking-tight sm:text-3xl text-foreground">
                Build Balanced, Exam-Ready Question Papers in Seconds
              </h2>
              <p className="text-sm text-muted-foreground leading-relaxed">
                Configure CBSE/ICSE board rules, cognitive difficulty ratios (Bloom's Taxonomy), and chapter marks distribution.
                The Gemini AI engine retrieves, verifies, and typesets complete question papers with full LaTeX support.
              </p>
            </div>
            <div className="flex flex-col sm:flex-row gap-3">
              <Button asChild className="gap-2 px-6 py-5 text-base font-semibold shadow-lg" size="lg">
                <Link href="/papers/new">
                  <Sparkles className="h-5 w-5" />
                  Launch Paper Studio
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Main Grid: Recent Papers & System Status */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Recent Papers (2 Cols) */}
        <Card className="lg:col-span-2 border-border bg-card/60 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle className="text-lg font-semibold">Recent Question Papers</CardTitle>
              <CardDescription>Recently generated and edited exam papers</CardDescription>
            </div>
            <Button asChild size="sm" variant="ghost">
              <Link className="gap-1 text-xs font-medium" href="/papers">
                View All
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-border">
              {RECENT_PAPERS.map((paper) => (
                <div
                  className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 hover:bg-muted/40 transition-colors"
                  key={paper.id}
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-foreground hover:underline cursor-pointer">
                        <Link href={`/papers/new?paperId=${paper.id}`}>
                          {paper.title}
                        </Link>
                      </span>
                      {paper.status === "approved" ? (
                        <Badge className="bg-emerald-500/10 text-emerald-500 border-emerald-500/20 text-[10px] font-medium" variant="outline">
                          Approved
                        </Badge>
                      ) : paper.status === "review" ? (
                        <Badge className="bg-amber-500/10 text-amber-500 border-amber-500/20 text-[10px] font-medium" variant="outline">
                          In Review
                        </Badge>
                      ) : (
                        <Badge className="bg-slate-500/10 text-slate-400 border-slate-500/20 text-[10px] font-medium" variant="outline">
                          Draft
                        </Badge>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                      <span className="font-semibold text-foreground/80">{paper.board} {paper.grade}</span>
                      <span>•</span>
                      <span>{paper.subject}</span>
                      <span>•</span>
                      <span>{paper.marks} Marks ({paper.questionsCount} Qs, {paper.sections} Sec)</span>
                      <span>•</span>
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {paper.updatedAt}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Button asChild size="sm" variant="outline">
                      <Link href="/papers/new">
                        Open Studio
                      </Link>
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* System & Services Status (1 Col) */}
        <div className="space-y-6">
          <Card className="border-border bg-card/60 shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Activity className="h-4 w-4 text-primary" />
                Live Architecture Status
              </CardTitle>
              <CardDescription className="text-xs">
                Local development services and cloud engines
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3.5 text-xs">
              <div className="flex items-center justify-between rounded-lg border border-border bg-muted/30 p-2.5">
                <div className="flex items-center gap-2.5">
                  <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  <div>
                    <div className="font-medium text-foreground">FastAPI Backend</div>
                    <div className="text-[11px] text-muted-foreground">Port 8000 • Healthy</div>
                  </div>
                </div>
                <Badge variant="outline" className="text-[10px] text-emerald-500 border-emerald-500/30">
                  Online
                </Badge>
              </div>

              <div className="flex items-center justify-between rounded-lg border border-border bg-muted/30 p-2.5">
                <div className="flex items-center gap-2.5">
                  <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  <div>
                    <div className="font-medium text-foreground">PostgreSQL & Alembic</div>
                    <div className="text-[11px] text-muted-foreground">Rev 0040 • RLS Enabled</div>
                  </div>
                </div>
                <Badge variant="outline" className="text-[10px] text-emerald-500 border-emerald-500/30">
                  Connected
                </Badge>
              </div>

              <div className="flex items-center justify-between rounded-lg border border-border bg-muted/30 p-2.5">
                <div className="flex items-center gap-2.5">
                  <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  <div>
                    <div className="font-medium text-foreground">Redis & Celery Queue</div>
                    <div className="text-[11px] text-muted-foreground">Port 6379 • Session Store</div>
                  </div>
                </div>
                <Badge variant="outline" className="text-[10px] text-emerald-500 border-emerald-500/30">
                  Healthy
                </Badge>
              </div>

              <div className="flex items-center justify-between rounded-lg border border-border bg-muted/30 p-2.5">
                <div className="flex items-center gap-2.5">
                  <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  <div>
                    <div className="font-medium text-foreground">Google Gemini AI Engine</div>
                    <div className="text-[11px] text-muted-foreground">gemini-2.5-flash • Solver Active</div>
                  </div>
                </div>
                <Badge variant="outline" className="text-[10px] text-emerald-500 border-emerald-500/30">
                  Ready
                </Badge>
              </div>
            </CardContent>
          </Card>

          {/* Quick Shortcuts */}
          <Card className="border-border bg-card/60 shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold">Quick Operations</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-2 text-xs">
              <Button asChild className="h-auto flex-col items-start p-3 text-left" variant="outline">
                <Link href="/question-bank">
                  <Library className="h-4 w-4 mb-1 text-primary" />
                  <span className="font-medium">Question Bank</span>
                  <span className="text-[10px] text-muted-foreground">Search & Tag</span>
                </Link>
              </Button>
              <Button asChild className="h-auto flex-col items-start p-3 text-left" variant="outline">
                <Link href="/admin/templates">
                  <Layers className="h-4 w-4 mb-1 text-primary" />
                  <span className="font-medium">Templates</span>
                  <span className="text-[10px] text-muted-foreground">Exam Blueprints</span>
                </Link>
              </Button>
              <Button asChild className="h-auto flex-col items-start p-3 text-left" variant="outline">
                <Link href="/admin/approvals">
                  <ShieldCheck className="h-4 w-4 mb-1 text-primary" />
                  <span className="font-medium">Approvals</span>
                  <span className="text-[10px] text-muted-foreground">Review Queue</span>
                </Link>
              </Button>
              <Button asChild className="h-auto flex-col items-start p-3 text-left" variant="outline">
                <Link href="/platform/curriculum">
                  <BookOpen className="h-4 w-4 mb-1 text-primary" />
                  <span className="font-medium">Curriculum</span>
                  <span className="text-[10px] text-muted-foreground">CBSE Hierarchy</span>
                </Link>
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
