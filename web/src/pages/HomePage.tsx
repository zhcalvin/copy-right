import type { Project } from "../api/types";
import { NewProjectForm } from "../components/NewProjectForm";
import { ProjectList } from "../components/ProjectList";
import { useI18n } from "../i18n";
import { ERROR_BOX } from "../lib/ui";

export type ProjectsState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; projects: Project[] };

export function HomePage({ state }: { state: ProjectsState }) {
  const { t } = useI18n();
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">{t("home.title")}</h1>
        {state.kind === "ready" && <NewProjectForm />}
      </div>
      {state.kind === "loading" && <p className="text-sm text-stone-500">{t("app.loading")}</p>}
      {state.kind === "error" && (
        <div className={ERROR_BOX}>
          {state.message}. {t("app.startBackend")}
          <code className="ml-1 font-mono">uv run uvicorn app.main:app --reload --port 8000</code>
        </div>
      )}
      {state.kind === "ready" && <ProjectList projects={state.projects} />}
    </>
  );
}
