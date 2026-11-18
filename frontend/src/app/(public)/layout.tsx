import Link from "next/link";
import type { ReactNode } from "react";

export default function PublicLayout({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,hsl(var(--background)),hsl(var(--muted)))] px-4 py-10">
      <div className="w-full max-w-md">
        <Link className="mb-6 flex items-center justify-center gap-3 text-foreground" href="/login">
          <span className="flex h-9 w-9 items-center justify-center rounded-md bg-primary text-sm font-semibold text-primary-foreground">
            PG
          </span>
          <span className="text-xl font-semibold">PaperGen</span>
        </Link>
        {children}
      </div>
    </main>
  );
}
