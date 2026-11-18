import * as React from "react";
import { cn } from "@/lib/utils";

const Alert = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      className={cn("rounded-md border border-border bg-secondary/55 px-4 py-3 text-sm text-secondary-foreground", className)}
      ref={ref}
      role="alert"
      {...props}
    />
  ),
);
Alert.displayName = "Alert";

export { Alert };
