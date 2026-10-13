import { useEffect, useState } from "react";

import { api } from "./api/client";
import type { Health } from "./api/types";
import { Header } from "./components/Header";
import { HomePage, type ProjectsState } from "./pages/HomePage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupPage } from "./pages/SetupPage";
import { navigate, usePathname } from "./router";

export default function App() {
  const pathname = usePathname();
  const [health, setHealth] = useState<Health | null>(null);
  const [projects, setProjects] = useState<ProjectsState>({ kind: "loading" });
  const [configured, setConfigured] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.health(), api.setupStatus()])
      .then(([h, status]) => {
        if (cancelled) return;
        setHealth(h);
        setConfigured(status.configured);
      })
      .catch((error: Error) => {
        if (!cancelled) setProjects({ kind: "error", message: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // No configuration yet: the first visit goes straight to the wizard.
  useEffect(() => {
    if (configured === false && pathname !== "/setup") navigate("/setup", { replace: true });
  }, [configured, pathname]);

  useEffect(() => {
    if (pathname !== "/" || !configured) return;
    let cancelled = false;
    api
      .listProjects()
      .then((list) => !cancelled && setProjects({ kind: "ready", projects: list }))
      .catch((error: Error) => !cancelled && setProjects({ kind: "error", message: error.message }));
    return () => {
      cancelled = true;
    };
  }, [pathname, configured]);

  return (
    <div className="flex flex-col min-h-screen">
      <Header health={health} backendDown={projects.kind === "error" && health === null} />
      <main className="flex flex-col gap-6 mx-auto px-6 py-10 w-full max-w-6xl">
        {pathname === "/setup" && <SetupPage onConfigured={() => setConfigured(true)} />}
        {pathname === "/settings" && <SettingsPage />}
        {pathname !== "/setup" && pathname !== "/settings" && <HomePage state={projects} />}
      </main>
    </div>
  );
}
