import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";

import { api, projectFileUrl } from "../api/client";
import type { Project, ProviderSpec, Render, RenderQuality, Storyboard, Treatment } from "../api/types";
import { labelFor, useI18n } from "../i18n";
import { BTN_LINK, BTN_PRIMARY, BTN_SECONDARY, CARD, ERROR_BOX, INPUT, TAG } from "../lib/ui";
import { PAID_TREATMENTS } from "./edits";
import { RenderShotRow } from "./RenderShotRow";
import { TREATMENT_TAG } from "./ShotCard";
import { isActive } from "./useRender";

interface RenderPanelProps {
  project: Project;
  storyboard: Storyboard;
  render: Render | null;
  onRender: (render: Render) => void;
}

export function RenderPanel({ project, storyboard, render, onRender }: RenderPanelProps) {
  const { t } = useI18n();
  const [quality, setQuality] = useState<RenderQuality>("preview");
  const [approve, setApprove] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [providers, setProviders] = useState<ProviderSpec[]>([]);
  const [startingNew, setStartingNew] = useState(false);

  useEffect(() => {
    api.providers().then(setProviders).catch(() => undefined);
  }, []);

  const paid = storyboard.shots.filter(
    (s) => PAID_TREATMENTS.has(s.edit.treatment) || (s.edit.voice_changed && s.edit.treatment !== "removed"),
  );
  const counts = paid.reduce<Partial<Record<Treatment, number>>>(
    (acc, s) => ({ ...acc, [s.edit.treatment]: (acc[s.edit.treatment] ?? 0) + 1 }),
    {},
  );
  const seconds = paid.reduce((sum, s) => sum + (s.source ? s.source.end - s.source.start : s.generation.duration ?? 4), 0);

  const run = async (action: () => Promise<Render>) => {
    setBusy(true);
    setError(null);
    try {
      onRender(await action());
      setStartingNew(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const act = (action: string, body?: unknown) =>
    render && run(() => api.renderAction(project.id, render.id, action, body));

  const showForm = !render || startingNew || render.status === "cancelled" || (render.status === "failed" && !render.output);
  if (showForm) {
    return (
      <section className={CARD}>
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold">{t("render.title")}</h2>
          <p className="text-sm text-stone-500">{t("render.hint")}</p>
        </div>
        {paid.length === 0 ? (
          <p className="text-sm text-stone-500">{t("render.nothing")}</p>
        ) : (
          <p className="flex flex-wrap items-center gap-1.5 text-sm">
            {t("render.summary", { count: paid.length, seconds: seconds.toFixed(1) })}
            {(Object.entries(counts) as [Treatment, number][]).map(([treatment, count]) => (
              <span key={treatment} className={clsx(TAG, TREATMENT_TAG[treatment])}>
                {labelFor(t, "treatment", treatment)} × {count}
              </span>
            ))}
          </p>
        )}
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-stone-700">{t("render.quality")}</span>
            <select className={INPUT} value={quality} onChange={(event) => setQuality(event.target.value as RenderQuality)}>
              <option value="preview">{t("render.quality.preview")}</option>
              <option value="final">{t("render.quality.final")}</option>
            </select>
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm text-stone-700">
            <input
              type="checkbox"
              className="shrink-0 size-4 accent-brand-600"
              checked={approve}
              onChange={(event) => setApprove(event.target.checked)}
            />
            {t("render.approveFrames")}
          </label>
        </div>
        {(error || render?.status === "failed") && (
          <div className={ERROR_BOX}>{error ?? render?.error}</div>
        )}
        <div className="flex gap-2">
          <button
            type="button"
            className={BTN_PRIMARY}
            disabled={busy}
            onClick={() => run(() => api.startRender(project.id, quality, approve))}
          >
            {paid.length === 0 ? t("render.startKeep") : t("render.start")}
          </button>
          {startingNew && (
            <button type="button" className={BTN_SECONDARY} onClick={() => setStartingNew(false)}>
              {t("upload.cancel")}
            </button>
          )}
        </div>
      </section>
    );
  }

  return (
    <RenderProgress
      project={project}
      storyboard={storyboard}
      render={render}
      providers={providers}
      busy={busy}
      error={error}
      onAction={act}
      onNew={() => setStartingNew(true)}
    />
  );
}

interface RenderProgressProps {
  project: Project;
  storyboard: Storyboard;
  render: Render;
  providers: ProviderSpec[];
  busy: boolean;
  error: string | null;
  onAction: (action: string, body?: unknown) => void;
  onNew: () => void;
}

function RenderProgress({ project, storyboard, render, providers, busy, error, onAction, onNew }: RenderProgressProps) {
  const { t } = useI18n();
  const shots = useMemo(() => new Map(storyboard.shots.map((s) => [s.shot_id, s])), [storyboard.shots]);
  const entries = Object.entries(render.shots).filter(([, s]) => PAID_TREATMENTS.has(s.treatment) || s.voice);
  const done = entries.filter(([, s]) => s.status === "done").length;
  const waitingFrames = entries.filter(([, s]) => s.status === "frame_ready").length;
  const active = isActive(render);

  return (
    <section className={CARD}>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">{t("render.title")}</h2>
        <span className="text-sm text-stone-500">
          {t("render.progress", { done, total: entries.length, version: render.storyboard_version })} ·{" "}
          {t(`render.quality.${render.quality}`)}
        </span>
        <span
          className={clsx(
            TAG,
            render.status === "done" ? "text-brand-700 bg-brand-50" : "text-amber-800 bg-amber-100",
            active && render.status !== "waiting" && "animate-pulse",
          )}
        >
          {labelFor(t, "render.status", render.status)}
        </span>
        <div className="flex gap-2 ml-auto">
          {waitingFrames > 1 && (
            <button type="button" className={BTN_PRIMARY} disabled={busy} onClick={() => onAction("approve-all")}>
              {t("render.approveAll", { count: waitingFrames })}
            </button>
          )}
          {active ? (
            <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={() => onAction("cancel")}>
              {t("render.cancel")}
            </button>
          ) : (
            <button type="button" className={BTN_SECONDARY} onClick={onNew}>
              {t("render.again")}
            </button>
          )}
        </div>
      </div>
      {render.status === "waiting" && waitingFrames > 0 && <p className="text-sm text-stone-600">{t("render.waitingHint")}</p>}
      {error && <div className={ERROR_BOX}>{error}</div>}
      {render.status === "failed" && render.error && <div className={ERROR_BOX}>{render.error}</div>}

      {render.status === "done" && render.output && project.source_file && (
        <div className="flex flex-col gap-2">
          <div className="grid gap-3 md:grid-cols-2">
            <figure className="flex flex-col gap-1">
              <figcaption className="text-xs font-medium text-stone-500">{t("render.original")}</figcaption>
              <video className="w-full bg-stone-900 rounded-lg aspect-video" src={projectFileUrl(project.id, project.source_file)} controls preload="metadata" />
            </figure>
            <figure className="flex flex-col gap-1">
              <figcaption className="text-xs font-medium text-stone-500">{t("render.result")}</figcaption>
              <video className="w-full bg-stone-900 rounded-lg aspect-video" src={projectFileUrl(project.id, render.output)} controls preload="metadata" />
            </figure>
          </div>
          <a className={clsx(BTN_LINK, "self-start")} href={projectFileUrl(project.id, render.output)} download={`${project.name}.mp4`}>
            ⬇ {t("render.download")}
          </a>
        </div>
      )}

      {entries.length > 0 && (
        <ul className="flex flex-col gap-2 max-h-128 overflow-y-auto">
          {entries.map(([shotId, state]) => (
            <RenderShotRow
              key={shotId}
              projectId={project.id}
              shotId={shotId}
              shot={shots.get(shotId)}
              state={state}
              providers={providers}
              busy={busy}
              onAction={onAction}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
