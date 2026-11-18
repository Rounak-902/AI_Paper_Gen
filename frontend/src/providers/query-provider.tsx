"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function QueryProvider({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            gcTime: 5 * 60 * 1000,
            refetchOnReconnect: true,
            retry: (failureCount, error) => {
              const status = error instanceof Error && "status" in error ? Number(error.status) : 0;

              return failureCount < 2 && (status === 0 || status >= 500 || status === 429);
            },
            staleTime: 30 * 1000,
          },
          mutations: {
            retry: false,
          },
        },
      }),
  );

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
