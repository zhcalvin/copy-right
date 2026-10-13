import { useCallback, useState } from "react";

import { api } from "../api/client";
import type { TestState } from "./ConnectionRow";

export function useConnectionTests() {
  const [tests, setTests] = useState<Record<string, TestState>>({});

  const run = useCallback(async (providerId: string) => {
    setTests((prev) => ({ ...prev, [providerId]: { kind: "running" } }));
    const result = await api.testProvider(providerId).catch((error: Error) => ({
      provider: providerId,
      status: "failed" as const,
      error_type: null,
      message: error.message,
    }));
    setTests((prev) => ({ ...prev, [providerId]: { kind: "done", result } }));
  }, []);

  const stateOf = (providerId: string): TestState => tests[providerId] ?? { kind: "idle" };

  return { run, stateOf };
}
