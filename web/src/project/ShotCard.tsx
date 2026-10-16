import clsx from "clsx";
import { useRef } from "react";

import { projectFileUrl } from "../api/client";
import type { Character, Shot, Treatment } from "../api/types";
import { labelFor, useI18n } from "../i18n";
import { TAG } from "../lib/ui";
import { shotSeconds } from "./edits";
import { formatTimecode } from "./format";

export const TREATMENT_TAG: Record<Treatment, string> = {
  keep: "text-brand-700 bg-brand-50",
  trim: "text-teal-700 bg-teal-50",
  swap: "text-amber-800 bg-amber-100",
  edit: "text-orange-800 bg-orange-100",
  motion: "text-fuchsia-800 bg-fuchsia-100",
  regenerate: "text-rose-800 bg-rose-100",
  new: "text-indigo-800 bg-indigo-100",
  removed: "text-stone-500 bg-stone-200 line-through",
};

const TREATMENT_RING: Partial<Record<Treatment, string>> = {
  trim: "ring-teal-400",
  swap: "ring-amber-400",
  edit: "ring-orange-400",
  motion: "ring-fuchsia-400",
  regenerate: "ring-rose-400",
  new: "ring-indigo-400",
};

interface ShotCardProps {
  projectId: string;
  shot: Shot;
  characters: Map<string, Character>;
  dimmed: boolean;
  highlighted?: boolean;
  /** Replaces the source clip, e.g. with a generated result. */
  resultClip?: string | null;
  onOpen?: () => void;
}

export function ShotCard({ projectId, shot, characters, dimmed, highlighted = false, resultClip, onOpen }: ShotCardProps) {
  const { t } = useI18n();
  const video = useRef<HTMLVideoElement>(null);
  const source = shot.source;
  const keyframes = source?.keyframes ?? [];
  const poster = keyframes[Math.floor(keyframes.length / 2)];
  const seconds = shotSeconds(shot);
  const treatment = shot.edit.treatment;
  const removed = treatment === "removed";
  const clip = resultClip ?? source?.clip;

  const play = () => {
    const element = video.current;
    if (!element) return;
    element.currentTime = 0;
    void element.play().catch(() => undefined); // autoplay can be refused; the poster stays
  };
  const stop = () => video.current?.pause();

  return (
    <article
      className={clsx(
        "flex flex-col overflow-hidden bg-white border border-brand-100 rounded-xl shadow-sm transition",
        TREATMENT_RING[treatment] && `ring-2 ${TREATMENT_RING[treatment]}`,
        highlighted && "ring-4 ring-sky-400",
        dimmed && "opacity-30",
        removed && "opacity-50 grayscale",
        onOpen && "cursor-pointer hover:shadow-md",
      )}
      onMouseEnter={play}
      onMouseLeave={stop}
      onFocus={play}
      onBlur={stop}
      onClick={onOpen}
      onKeyDown={(event) => event.key === "Enter" && onOpen?.()}
      tabIndex={0}
    >
      <div className="relative bg-stone-900 aspect-video">
        {clip ? (
          <video
            ref={video}
            className="size-full object-contain"
            src={projectFileUrl(projectId, clip)}
            poster={poster ? projectFileUrl(projectId, poster) : undefined}
            muted
            loop
            playsInline
            preload="none"
          />
        ) : (
          <div className="flex items-center justify-center size-full text-sm text-indigo-100 bg-indigo-950">
            {t("editor.newShot")}
          </div>
        )}
        <span className="absolute top-2 left-2 px-1.5 py-0.5 font-mono text-xs text-white bg-black/60 rounded">
          {shot.shot_id}
        </span>
        {seconds !== null && (
          <span className="absolute right-2 bottom-2 px-1.5 py-0.5 font-mono text-xs text-white bg-black/60 rounded">
            {t("shot.seconds", { seconds: seconds.toFixed(1) })}
          </span>
        )}
      </div>
      <div className="flex flex-col flex-1 gap-2 p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {source && (
            <span className="mr-auto font-mono text-xs text-stone-500">
              {formatTimecode(source.start)}–{formatTimecode(source.end)}
            </span>
          )}
          {shot.visual.shot_size && (
            <span className={clsx(TAG, "text-sky-700 bg-sky-50")}>{labelFor(t, "size", shot.visual.shot_size)}</span>
          )}
          {shot.visual.camera_motion && (
            <span className={clsx(TAG, "text-violet-700 bg-violet-50")}>
              {labelFor(t, "motion", shot.visual.camera_motion)}
            </span>
          )}
        </div>
        <p className={clsx("text-sm leading-relaxed", !shot.narrative.description && "text-stone-400")}>
          {shot.narrative.description || t("board.noDescription")}
        </p>
        {shot.narrative.dialogue.length > 0 && (
          <ul className="flex flex-col gap-1 pl-2 text-sm text-stone-600 border-l-2 border-amber-200" aria-label={t("shot.dialogue")}>
            {shot.narrative.dialogue.map((line, index) => (
              <li key={index}>“{line.text}”</li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-1.5 mt-auto pt-1">
          {shot.assets.character_ids.map((id) => (
            <span key={id} className={clsx(TAG, "text-stone-600 bg-stone-100")}>
              {characters.get(id)?.name ?? id}
            </span>
          ))}
          {shot.edit.voice_changed && !removed && (
            <span className={clsx(TAG, "ml-auto text-sky-800 bg-sky-100")} title={t("voice.hint")}>
              🎙 {t("voice.badge")}
            </span>
          )}
          <span
            className={clsx(TAG, !shot.edit.voice_changed && "ml-auto", TREATMENT_TAG[treatment])}
            title={shot.edit.treatment_reason ?? undefined}
          >
            {shot.edit.treatment_locked && "🔒 "}
            {labelFor(t, "treatment", treatment)}
          </span>
        </div>
        {treatment !== "keep" && shot.edit.treatment_reason && (
          <p className="text-xs text-stone-500">{shot.edit.treatment_reason}</p>
        )}
      </div>
    </article>
  );
}
