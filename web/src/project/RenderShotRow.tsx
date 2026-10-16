import clsx from "clsx";
import { useState } from "react";

import { projectFileUrl } from "../api/client";
import type { ProviderSpec, Shot, ShotRender } from "../api/types";
import { labelFor, localized, useI18n } from "../i18n";
import { BTN_PRIMARY, BTN_SECONDARY, INPUT, INPUT_INLINE, TAG } from "../lib/ui";
import { TREATMENT_TAG } from "./ShotCard";

const STEP_CAPABILITY = {
  frame: "image_edit",
  video: "video_edit_v2v",
  i2v: "image_to_video",
  tts: "tts",
  lipsync: "lipsync",
} as const;

interface RenderShotRowProps {
  projectId: string;
  shotId: string;
  shot: Shot | undefined;
  state: ShotRender;
  providers: ProviderSpec[];
  busy: boolean;
  onAction: (action: string, body?: unknown) => void;
}

export function RenderShotRow({ projectId, shotId, shot, state, providers, busy, onAction }: RenderShotRowProps) {
  const { t, lang } = useI18n();
  const [hint, setHint] = useState("");
  const capability = state.step ? STEP_CAPABILITY[state.step] : "video_edit_v2v";
  const alternatives = providers.filter((p) => p.implemented_capabilities.includes(capability));
  const [provider, setProvider] = useState(state.provider ?? alternatives[0]?.id ?? "");
  const keyframes = shot?.source?.keyframes ?? [];
  const preview = state.first_frame ?? keyframes[Math.floor(keyframes.length / 2)];

  return (
    <li className="flex gap-3 p-3 bg-white rounded-lg border border-stone-100">
      <div className="relative shrink-0 overflow-hidden w-40 bg-stone-900 rounded-md aspect-video">
        {state.status === "done" && state.clip ? (
          <video
            className="size-full object-cover"
            src={projectFileUrl(projectId, state.clip)}
            muted
            loop
            playsInline
            autoPlay
          />
        ) : preview ? (
          <img src={projectFileUrl(projectId, preview)} alt="" className="size-full object-cover" />
        ) : null}
        <span className="absolute top-1 left-1 px-1 font-mono text-xs text-white bg-black/60 rounded">{shotId}</span>
      </div>
      <div className="flex flex-col flex-1 gap-1.5 min-w-0 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className={clsx(TAG, TREATMENT_TAG[state.treatment])}>{labelFor(t, "treatment", state.treatment)}</span>
          <span
            className={clsx(
              "text-xs font-medium",
              state.status === "failed" ? "text-red-700" : state.status === "done" ? "text-brand-700" : "text-stone-500",
            )}
          >
            {state.status === "running" && state.step
              ? t(`render.step.${state.step}`)
              : labelFor(t, "render.shot", state.status)}
          </span>
          {state.pieces > 1 && <span className="text-xs text-stone-400">{t("render.pieces", { count: state.pieces })}</span>}
          {state.reused && <span className={clsx(TAG, "text-stone-600 bg-stone-100")}>♻ {t("render.reused")}</span>}
          {state.voice && <span className={clsx(TAG, "text-sky-800 bg-sky-100")}>🎙 {t("voice.badge")}</span>}
        </div>
        <p className="text-stone-600 line-clamp-2">{shot?.narrative.description}</p>

        {state.status === "frame_ready" && (
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={BTN_PRIMARY} disabled={busy} onClick={() => onAction(`shots/${shotId}/approve`)}>
              {t("render.approve")}
            </button>
            <input
              className={clsx(INPUT, "flex-1 min-w-40")}
              placeholder={t("render.hintPlaceholder")}
              value={hint}
              onChange={(event) => setHint(event.target.value)}
            />
            <button
              type="button"
              className={BTN_SECONDARY}
              disabled={busy}
              onClick={() => onAction(`shots/${shotId}/redo-frame`, { prompt: hint })}
            >
              {t("render.redo")}
            </button>
          </div>
        )}

        {state.status === "failed" && (
          <div className="flex flex-col gap-2">
            <p className="text-xs text-red-700 break-words">
              {labelFor(t, "error", state.error_type ?? "provider_error")}: {state.error}
            </p>
            {state.error_type === "content_rejected" && (
              <p className="text-xs text-stone-500">{t("render.rejectedHint")}</p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              {alternatives.length > 1 && (
                <select
                  className={INPUT_INLINE}
                  aria-label={t("render.provider")}
                  value={provider}
                  onChange={(event) => setProvider(event.target.value)}
                >
                  {alternatives.map((p) => (
                    <option key={p.id} value={p.id}>
                      {localized(p, "name", lang)}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                className={BTN_SECONDARY}
                disabled={busy}
                onClick={() => onAction(`shots/${shotId}/retry`, alternatives.length > 1 ? { provider } : {})}
              >
                {t("render.retry")}
              </button>
              {state.step === "lipsync" && state.audio && (
                <button
                  type="button"
                  className={BTN_SECONDARY}
                  disabled={busy}
                  onClick={() => onAction(`shots/${shotId}/skip-lipsync`)}
                >
                  {t("render.skipLipsync")}
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </li>
  );
}
