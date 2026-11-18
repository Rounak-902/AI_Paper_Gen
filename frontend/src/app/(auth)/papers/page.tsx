"use client";

import Link from "next/link";
import { useState } from "react";
import {
  ArrowUpDown,
  BookOpen,
  Calendar,
  CheckCircle2,
  Clock,
  Download,
  Eye,
  FileCheck,
  FileDown,
  FileText,
  Filter,
  Layers,
  MoreVertical,
  Plus,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const INITIAL_PAPERS = [
  {
    id: "cbse-10-sci-midterm-2026",
    title: "CBSE Class 10 Science - Mid-Term Examination 2025-26",
    board: "CBSE",
    grade: "Class 10",
    subject: "Science",
    marks: 80,
    timeMinutes: 180,
    sections: 5,
    questionsCount: 39,
    status: "approved",
    difficulty: { easy: 25, medium: 50, hard: 25 },
    createdAt: "2026-09-28",
  },
  {
    id: "cbse-10-math-preboard-2026",
    title: "Class 10 Mathematics Standard - Pre-Board Examination",
    board: "CBSE",
    grade: "Class 10",
    subject: "Mathematics",
    marks: 80,
    timeMinutes: 180,
    sections: 5,
    questionsCount: 38,
    status: "draft",
    difficulty: { easy: 20, medium: 55, hard: 25 },
    createdAt: "2026-09-29",
  },
  {
    id: "unit-test-chem-reactions",
    title: "Unit Assessment: Chemical Reactions and Equations",
    board: "CBSE",
    grade: "Class 10",
    subject: "Science",
    marks: 25,
    timeMinutes: 60,
    sections: 3,
    questionsCount: 15,
    status: "approved",
    difficulty: { easy: 30, medium: 50, hard: 20 },
    createdAt: "2026-09-27",
  },
  {
    id: "diag-test-acids-bases",
    title: "Diagnostic Test: Acids, Bases and Salts",
    board: "CBSE",
    grade: "Class 10",
    subject: "Science",
    marks: 20,
    timeMinutes: 45,
    sections: 2,
    questionsCount: 10,
    status: "review",
    difficulty: { easy: 40, medium: 40, hard: 20 },
    createdAt: "2026-09-26",
  },
  {
    id: "icse-10-physics-term1",
    title: "ICSE Class 10 Physics - Comprehensive Term 1",
    board: "ICSE",
    grade: "Class 10",
    subject: "Physics",
    marks: 80,
    timeMinutes: 120,
    sections: 2,
    questionsCount: 32,
    status: "approved",
    difficulty: { easy: 20, medium: 60, hard: 20 },
    createdAt: "2026-09-20",
  },
];

export default function PapersPage() {
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const filteredPapers = INITIAL_PAPERS.filter((paper) => {
    const matchesSearch =
      paper.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
      paper.subject.toLowerCase().includes(searchTerm.toLowerCase()) ||
      paper.board.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === "all" || paper.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  return (
    <div className="mx-auto max-w-[1500px] space-y-6">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Question Papers</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage, edit, export, and generate AI-verified question papers.
          </p>
        </div>
        <Button asChild className="gap-2 shadow-sm">
          <Link href="/papers/new">
            <Sparkles className="h-4 w-4" />
            New Question Paper
          </Link>
        </Button>
      </div>

      {/* Filter and Search Bar */}
      <Card className="border-border bg-card/60">
        <CardContent className="p-4">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                className="pl-9"
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Search papers by title, subject, board..."
                value={searchTerm}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                className="text-xs h-8"
                onClick={() => setStatusFilter("all")}
                size="sm"
                variant={statusFilter === "all" ? "default" : "outline"}
              >
                All Papers ({INITIAL_PAPERS.length})
              </Button>
              <Button
                className="text-xs h-8"
                onClick={() => setStatusFilter("approved")}
                size="sm"
                variant={statusFilter === "approved" ? "default" : "outline"}
              >
                Approved
              </Button>
              <Button
                className="text-xs h-8"
                onClick={() => setStatusFilter("draft")}
                size="sm"
                variant={statusFilter === "draft" ? "default" : "outline"}
              >
                Drafts
              </Button>
              <Button
                className="text-xs h-8"
                onClick={() => setStatusFilter("review")}
                size="sm"
                variant={statusFilter === "review" ? "default" : "outline"}
              >
                In Review
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Papers Grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {filteredPapers.map((paper) => (
          <Card className="flex flex-col justify-between border-border bg-card/60 hover:shadow-md transition-shadow" key={paper.id}>
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-2">
                <Badge className="font-semibold text-[11px]" variant="secondary">
                  {paper.board} • {paper.grade}
                </Badge>
                {paper.status === "approved" ? (
                  <Badge className="bg-emerald-500/10 text-emerald-500 border-emerald-500/20 text-[10px]" variant="outline">
                    Approved
                  </Badge>
                ) : paper.status === "review" ? (
                  <Badge className="bg-amber-500/10 text-amber-500 border-amber-500/20 text-[10px]" variant="outline">
                    In Review
                  </Badge>
                ) : (
                  <Badge className="bg-slate-500/10 text-slate-400 border-slate-500/20 text-[10px]" variant="outline">
                    Draft
                  </Badge>
                )}
              </div>
              <CardTitle className="text-base font-semibold leading-snug mt-2 line-clamp-2">
                {paper.title}
              </CardTitle>
              <CardDescription className="text-xs text-muted-foreground flex items-center gap-1.5 mt-1">
                <span>{paper.subject}</span>
                <span>•</span>
                <span>{paper.marks} Marks</span>
                <span>•</span>
                <span>{paper.timeMinutes} Mins</span>
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4 pt-0">
              <div className="rounded-lg bg-muted/40 p-2.5 text-xs space-y-1.5">
                <div className="flex justify-between text-muted-foreground">
                  <span>Questions / Sections:</span>
                  <span className="font-medium text-foreground">{paper.questionsCount} Qs ({paper.sections} Sec)</span>
                </div>
                <div className="flex justify-between text-muted-foreground">
                  <span>Cognitive Balance:</span>
                  <span className="font-medium text-foreground">
                    E:{paper.difficulty.easy}% | M:{paper.difficulty.medium}% | H:{paper.difficulty.hard}%
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2 pt-2 border-t border-border">
                <Button asChild className="flex-1 text-xs gap-1.5" size="sm" variant="default">
                  <Link href="/papers/new">
                    <Sparkles className="h-3.5 w-3.5" />
                    Open Studio
                  </Link>
                </Button>
                <Button asChild size="sm" variant="outline">
                  <Link href="/papers/new">
                    <FileDown className="h-3.5 w-3.5" />
                  </Link>
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
