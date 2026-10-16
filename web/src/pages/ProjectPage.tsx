import { useState } from "react";

import type { Project, Storyboard } from "../api/types";
import { useI18n } from "../i18n";
import { BTN_LINK, ERROR_BOX } from "../lib/ui";
import { ParsePanel } from "../project/ParsePanel";
import { ParseProgress } from "../project/ParseProgress";
import { StoryboardBoard } from "../project/StoryboardBoard";
import { UploadPanel } from "../project/UploadPanel";
import { formatDuration } from "../project/format";
import { useProject } from "../project/useProject";
import { navigate } from "../router";

export function ProjectPage({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const { state, storyboard, replace, replaceStoryboard } = useProject(projectId);

  return (
    <>
      <button type="button" className={`${BTN_LINK} self-start`} onClick={() => navigate("/")}>
        ← {t("project.back")}
      </button>
      {state.kind === "loading" && <p className="text-sm text-stone-500">{t("app.loading")}</p>}
      {state.kind === "missing" && <div className={ERROR_BOX}>{t("project.notFound")}</div>}
      {state.kind === "error" && <div className={ERROR_BOX}>{state.message}</div>}
      {state.kind === "ready" && (
        <ProjectBody
          project={state.project}
          storyboard={storyboard}
          onProject={replace}
          onStoryboard={replaceStoryboard}
        />
      )}
    </>
  );
}

interface ProjectBodyProps {
  project: Project;
  storyboard: ReturnType<typeof useProject>["storyboard"];
  onProject: (project: Project) => void;
  onStoryboard: (storyboard: Storyboard) => void;
}

function ProjectBody({ project, storyboard, onProject, onStoryboard }: ProjectBodyProps) {
  const { t } = useI18n();
  const [replacing, setReplacing] = useState(false);
  const hasStoryboard = storyboard.kind === "ready";

  const uploaded = (next: Project) => {
    setReplacing(false);
    onProject(next);
  };

  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold">{project.name}</h1>
        {project.duration !== null && (
          <span className="text-sm text-stone-500">
            {t("project.meta", {
              duration: formatDuration(project.duration),
              width: project.width ?? "?",
              height: project.height ?? "?",
              fps: project.fps ?? "?",
            })}
          </span>
        )}
        <span className="px-2 py-0.5 text-xs text-brand-700 bg-brand-50 rounded-full">
          {t(`status.${project.status}`)}
        </span>
      </div>

      {(!project.has_video || replacing) && (
        <UploadPanel
          project={project}
          onUploaded={uploaded}
          onCancel={project.has_video ? () => setReplacing(false) : undefined}
        />
      )}
      {project.has_video && !replacing && project.status === "parsing" && <ParseProgress project={project} />}
      {project.has_video && !replacing && project.status !== "parsing" && (
        <ParsePanel
          project={project}
          hasStoryboard={hasStoryboard}
          onStarted={onProject}
          onReplaceVideo={() => setReplacing(true)}
        />
      )}

      {storyboard.kind === "loading" && <p className="text-sm text-stone-500">{t("app.loading")}</p>}
      {storyboard.kind === "error" && (
        <div className={ERROR_BOX}>
          {t("board.loadFailed")}: {storyboard.message}
        </div>
      )}
      {storyboard.kind === "ready" && (
        <StoryboardBoard projectId={project.id} storyboard={storyboard.storyboard} onChange={onStoryboard} />
      )}
    </>
  );
}
