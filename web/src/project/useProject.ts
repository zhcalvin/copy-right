import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { Project, Storyboard } from "../api/types";

const POLL_MS = 2000;

export type ProjectState =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error"; message: string }
  | { kind: "ready"; project: Project };

export type StoryboardState =
  | { kind: "none" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; storyboard: Storyboard };

/** Loads a project, polls it while it is parsing, and fetches the storyboard once ready. */
export function useProject(projectId: string) {
  const [state, setState] = useState<ProjectState>({ kind: "loading" });
  const [storyboard, setStoryboard] = useState<StoryboardState>({ kind: "none" });

  const load = useCallback(async () => {
    try {
      const project = await api.getProject(projectId);
      setState({ kind: "ready", project });
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setState({ kind: "missing" });
      else setState({ kind: "error", message: (error as Error).message });
    }
  }, [projectId]);

  useEffect(() => {
    setState({ kind: "loading" });
    setStoryboard({ kind: "none" });
    void load();
  }, [load]);

  const project = state.kind === "ready" ? state.project : null;
  const parsing = project?.status === "parsing";
  // Any status after parsing has a storyboard to show.
  const ready = project !== null && ["ready", "generating", "done", "failed"].includes(project.status);

  useEffect(() => {
    if (!parsing) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [parsing, load]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setStoryboard((current) => (current.kind === "ready" ? current : { kind: "loading" }));
    api
      .storyboard(projectId)
      .then((sb) => !cancelled && setStoryboard({ kind: "ready", storyboard: sb }))
      .catch((error: Error) => {
        if (cancelled) return;
        // A failed first parse has no storyboard yet; that is not an error worth showing.
        if (error instanceof ApiError && error.status === 404) setStoryboard({ kind: "none" });
        else setStoryboard({ kind: "error", message: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, [ready, projectId]);

  const replace = useCallback((next: Project) => setState({ kind: "ready", project: next }), []);
  const replaceStoryboard = useCallback((next: Storyboard) => setStoryboard({ kind: "ready", storyboard: next }), []);
  return { state, storyboard, replace, replaceStoryboard, reload: load };
}
