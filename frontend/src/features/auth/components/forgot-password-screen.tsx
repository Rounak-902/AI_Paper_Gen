"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { requestPasswordReset } from "@/features/auth/api";

const schema = z.object({
  email: z.string().email("Enter a valid email address."),
});

type ForgotPasswordValues = z.infer<typeof schema>;

export function ForgotPasswordScreen() {
  const [submitted, setSubmitted] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const {
    formState: { errors, isSubmitting },
    handleSubmit,
    register,
  } = useForm<ForgotPasswordValues>({
    defaultValues: {
      email: "",
    },
    resolver: zodResolver(schema),
  });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);

    try {
      await requestPasswordReset(values);
      setSubmitted(true);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "PaperGen could not start the reset flow.");
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reset Password</CardTitle>
        <CardDescription>Enter the email address on your PaperGen account.</CardDescription>
      </CardHeader>
      <CardContent>
        {submitted ? (
          <div className="space-y-5">
            <Alert>Check your inbox for password reset instructions.</Alert>
            <Button asChild className="w-full" variant="secondary">
              <Link href="/login">Back to sign in</Link>
            </Button>
          </div>
        ) : (
          <form className="space-y-5" onSubmit={onSubmit}>
            {formError ? <Alert>{formError}</Alert> : null}
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input autoComplete="email" id="email" type="email" {...register("email")} />
              {errors.email ? <p className="text-sm text-destructive">{errors.email.message}</p> : null}
            </div>
            <Button className="w-full" disabled={isSubmitting} type="submit">
              {isSubmitting ? "Sending" : "Send reset link"}
            </Button>
            <Link className="block text-center text-sm font-medium text-primary hover:underline" href="/login">
              Back to sign in
            </Link>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
