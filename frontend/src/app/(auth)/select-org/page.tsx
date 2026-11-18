import { Suspense } from "react";
import { SelectOrgScreen } from "@/features/auth/components/select-org-screen";

export default function SelectOrgPage() {
  return (
    <Suspense fallback={<div className="h-80 animate-pulse rounded-lg bg-muted" />}>
      <SelectOrgScreen />
    </Suspense>
  );
}
