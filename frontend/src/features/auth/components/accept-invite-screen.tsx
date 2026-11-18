"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { acceptInvite } from "@/features/auth/api";

const schema = z
  .object({
    name: z.string().min(2, "Enter your name."),
    password: z.string().min(12, "Password must be at least 12 characters."),
    confirmPassword: z.string().min(12, "Confirm your password."),
  })
  .refine((values) => values.password === values.confirmPassword, {
    message: "Passwords do not match.",
    path: ["confirmPassword"],
  });

type AcceptInviteValues = z.infer<typeof schema>;

export function AcceptInviteScreen() {
  const params = useParams<{ token: string }>();
  const router = useRouter();
  const token = Array.isArray(params.token) ? params.token[0] : params.token;
  const [formError, setFormError] = useState<string | null>(null);
  const {
    formState: { errors, isSubmitting },
    handleSubmit,
    register,
  } = useForm<AcceptInviteValues>({
    defaultValues: {
      confirmPassword: "",
      name: "",
      password: "",
    },
    resolver: zodResolver(schema),
  });

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);

    if (!token) {
      setFormError("This invitation link is missing a token.");
      return;
    }

    try {
      await acceptInvite({
        name: values.name,
        password: values.password,
        token,
      });
      router.push("/login");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "PaperGen could not accept this invitation.");
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Accept Invitation</CardTitle>
        <CardDescription>Create your password to activate the account.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-5" onSubmit={onSubmit}>
          {formError ? <Alert>{formError}</Alert> : null}
          <div className="space-y-2">
            <Label htmlFor="name">Name</Label>
            <Input autoComplete="name" id="name" {...register("name")} />
            {errors.name ? <p className="text-sm text-destructive">{errors.name.message}</p> : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input autoComplete="new-password" id="password" type="password" {...register("password")} />
            {errors.password ? <p className="text-sm text-destructive">{errors.password.message}</p> : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor="confirmPassword">Confirm Password</Label>
            <Input
              autoComplete="new-password"
              id="confirmPassword"
              type="password"
              {...register("confirmPassword")}
            />
            {errors.confirmPassword ? (
              <p className="text-sm text-destructive">{errors.confirmPassword.message}</p>
            ) : null}
          </div>
          <Button className="w-full" disabled={isSubmitting} type="submit">
            {isSubmitting ? "Activating" : "Activate account"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
