"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { login } from "@/features/auth/api";
import { useSessionStore } from "@/stores/session";

const schema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(8, "Password must be at least 8 characters."),
});

type LoginFormValues = z.infer<typeof schema>;

function safeNext(path: string | null) {
  if (!path || !path.startsWith("/") || path.startsWith("//")) {
    return "/select-org";
  }

  return path.startsWith("/login") || path.startsWith("/forgot-password") || path.startsWith("/accept-invite")
    ? "/select-org"
    : path;
}

export function LoginScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setAuthenticated = useSessionStore((state) => state.setAuthenticated);
  const [formError, setFormError] = useState<string | null>(null);
  const {
    formState: { errors, isSubmitting },
    handleSubmit,
    register,
    setValue,
  } = useForm<LoginFormValues>({
    defaultValues: {
      email: "",
      password: "",
    },
    resolver: zodResolver(schema),
  });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);

    try {
      const session = await login(values);
      setAuthenticated(session);
      router.push(session.activeOrgId ? safeNext(searchParams.get("next")) : "/select-org");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "PaperGen could not sign you in.");
    }
  });

  const handleQuickLogin = (email: string, password = "Papergen123!") => {
    setValue("email", email, { shouldValidate: true });
    setValue("password", password, { shouldValidate: true });
    setTimeout(() => {
      void onSubmit();
    }, 50);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sign In</CardTitle>
        <CardDescription>Use your PaperGen account to continue.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Quick Demo Login Preset Banner */}
        <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs space-y-2">
          <div className="font-semibold text-primary flex items-center justify-between">
            <span className="flex items-center gap-1.5"><span>⚡</span> Quick 1-Click Login:</span>
            <span className="text-[10px] text-muted-foreground">Select Persona</span>
          </div>
          <div className="space-y-1.5">
            <Button
              className="w-full text-xs h-8 bg-primary/10 hover:bg-primary/20 text-primary border border-primary/30 font-semibold justify-center"
              disabled={isSubmitting}
              onClick={() => handleQuickLogin("rounakpanigrahi902@gmail.com", "Papergen@2026")}
              size="sm"
              type="button"
              variant="outline"
            >
              👑 Rounak (Super Admin)
            </Button>
            <div className="grid grid-cols-2 gap-2">
              <Button
                className="text-xs h-8 bg-card text-foreground hover:bg-muted border border-border"
                disabled={isSubmitting}
                onClick={() => handleQuickLogin("admin@papergen.local", "Papergen123!")}
                size="sm"
                type="button"
                variant="outline"
              >
                Dev Admin
              </Button>
              <Button
                className="text-xs h-8 bg-card text-foreground hover:bg-muted border border-border"
                disabled={isSubmitting}
                onClick={() => handleQuickLogin("teacher@papergen.local", "Papergen123!")}
                size="sm"
                type="button"
                variant="outline"
              >
                Dev Teacher
              </Button>
            </div>
          </div>
        </div>

        <form className="space-y-4" onSubmit={onSubmit}>
          {formError ? <Alert>{formError}</Alert> : null}
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              autoComplete="email"
              id="email"
              type="email"
              {...register("email")}
              aria-invalid={Boolean(errors.email)}
            />
            {errors.email ? <p className="text-sm text-destructive">{errors.email.message}</p> : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              autoComplete="current-password"
              id="password"
              type="password"
              {...register("password")}
              aria-invalid={Boolean(errors.password)}
            />
            {errors.password ? <p className="text-sm text-destructive">{errors.password.message}</p> : null}
          </div>
          <Button className="w-full" disabled={isSubmitting} type="submit">
            {isSubmitting ? "Signing in..." : "Sign in"}
          </Button>
          <Link className="block text-center text-sm font-medium text-primary hover:underline" href="/forgot-password">
            Forgot password?
          </Link>
        </form>
      </CardContent>
    </Card>
  );
}
