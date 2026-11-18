import { Suspense } from "react";
import { LoginScreen } from "@/features/auth/components/login-screen";

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="h-80 animate-pulse rounded-lg bg-muted" />}>
      <LoginScreen />
    </Suspense>
  );
}
