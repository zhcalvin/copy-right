import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { Render } from "../api/types";

const POLL_MS = 2000;
const ACTIVE = new Set(["running", "waiting", "stitching"]);

export function isActive(render: Render | null): boolean {
  return render !== null && ACTIVE.has(render.status);
}

/** The project's latest render, polled while it is still going. */
export function useRender(projectId: string, onFinished?: () => void) {
  const [render, setRender] = useState<Render | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [latest] = await api.renders(projectId);
      setRender(latest ?? null);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const active = isActive(render);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, load]);

  // Tell the page when a render finishes so the project status refreshes.
  const status = render?.status;
  useEffect(() => {
    if (status === "done" || status === "failed") onFinished?.();
  }, [status, onFinished]);

  return { render, setRender, error, reload: load };
}
