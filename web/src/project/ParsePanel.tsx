import clsx from "clsx";
import { useState } from "react";

import { api, projectFileUrl } from "../api/client";
import type { ParseLanguage, Project } from "../api/types";
import { labelFor, useI18n } from "../i18n";
import { BTN_LINK, BTN_PRIMARY, BTN_SECONDARY, CARD, ERROR_BOX, INPUT } from "../lib/ui";
import { navigate } from "../router";

// PySceneDetect adaptive thresholds: lower finds more cuts (see app/tools/scene_detect.py).
const CUT_LEVELS = [
  { id: "fine", threshold: 2.0 },
  { id: "normal", threshold: 2.5 },
  { id: "coarse", threshold: 3.5 },
] as const;
type CutLevel = (typeof CUT_LEVELS)[number]["id"];
const LANGUAGES: ParseLanguage[] = ["en", "zh-CN"];

interface ParsePanelProps {
  project: Project;
  hasStoryboard: boolean;
  onStarted: (project: Project) => void;
  onReplaceVideo: () => void;
}

export function ParsePanel({ project, hasStoryboard, onStarted, onReplaceVideo }: ParsePanelProps) {
  const { t, lang } = useI18n();
  const [language, setLanguage] = useState<ParseLanguage>(lang);
  const [cuts, setCuts] = useState<CutLevel>("normal");
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // Once there is a storyboard the source and parse options fold into one line.
  const [expanded, setExpanded] = useState(!hasStoryboard || project.status === "failed");

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      const threshold = CUT_LEVELS.find((level) => level.id === cuts)?.threshold;
      onStarted(await api.parseProject(project.id, { threshold, language }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setStarting(false);
    }
  };

  if (!expanded) {
    return (
      <section className={clsx(CARD, "flex-row flex-wrap items-center gap-3 py-3")}>
        <span className="text-sm font-medium text-stone-700">{t("parse.source")}</span>
        <span className="text-sm text-stone-500">{t("parse.againHint")}</span>
        <button type="button" className={clsx(BTN_SECONDARY, "ml-auto")} onClick={() => setExpanded(true)}>
          {t("parse.showSource")}
        </button>
      </section>
    );
  }

  return (
    <section className={CARD}>
      <div className="grid gap-5 md:grid-cols-2">
        {project.source_file && (
          <video
            className="w-full bg-stone-900 rounded-lg aspect-video"
            src={projectFileUrl(project.id, project.source_file)}
            controls
            preload="metadata"
          />
        )}
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-lg font-semibold">{hasStoryboard ? t("parse.again") : t("parse.title")}</h2>
            <p className="text-sm text-stone-500">{hasStoryboard ? t("parse.againHint") : t("parse.hint")}</p>
          </div>
          {project.status === "failed" && (
            <div className={ERROR_BOX}>
              <p className="font-medium">
                {t("parse.failed")}: {labelFor(t, "error", project.error_type ?? "provider_error")}
              </p>
              {project.error && <p className="mt-1 break-words">{project.error}</p>}
              {project.error_type === "not_configured" || project.error_type === "auth_error" ? (
                <button type="button" className={`${BTN_LINK} mt-2`} onClick={() => navigate("/settings")}>
                  {t("parse.settings")}
                </button>
              ) : null}
            </div>
          )}
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-stone-700">{t("parse.language")}</span>
            <select
              className={INPUT}
              value={language}
              onChange={(event) => setLanguage(event.target.value as ParseLanguage)}
            >
              {LANGUAGES.map((code) => (
                <option key={code} value={code}>
                  {t(`lang.${code}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-stone-700">{t("parse.cuts")}</span>
            <select className={INPUT} value={cuts} onChange={(event) => setCuts(event.target.value as CutLevel)}>
              {CUT_LEVELS.map((level) => (
                <option key={level.id} value={level.id}>
                  {t(`parse.cuts.${level.id}`)}
                </option>
              ))}
            </select>
          </label>
          {error && <div className={ERROR_BOX}>{error}</div>}
          <div className="flex flex-wrap gap-2 mt-auto">
            <button type="button" className={BTN_PRIMARY} disabled={starting} onClick={start}>
              {hasStoryboard ? t("parse.again") : t("parse.start")}
            </button>
            <button type="button" className={BTN_SECONDARY} disabled={starting} onClick={onReplaceVideo}>
              {t("upload.replace")}
            </button>
            {hasStoryboard && (
              <button type="button" className={clsx(BTN_LINK, "ml-auto")} onClick={() => setExpanded(false)}>
                {t("parse.hideSource")}
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
