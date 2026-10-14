import clsx from "clsx";
import { useRef } from "react";

import { projectFileUrl } from "../api/client";
import type { Character, Shot } from "../api/types";
import { labelFor, useI18n } from "../i18n";
import { TAG } from "../lib/ui";
import { formatTimecode } from "./format";

interface ShotCardProps {
  projectId: string;
  shot: Shot;
  characters: Map<string, Character>;
  dimmed: boolean;
}

export function ShotCard({ projectId, shot, characters, dimmed }: ShotCardProps) {
  const { t } = useI18n();
  const video = useRef<HTMLVideoElement>(null);
  const source = shot.source;
  const keyframes = source?.keyframes ?? [];
  const poster = keyframes[Math.floor(keyframes.length / 2)];
  const seconds = source ? (source.end - source.start).toFixed(1) : null;

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
        "flex flex-col overflow-hidden bg-white border border-brand-100 rounded-xl shadow-sm transition-opacity",
        dimmed && "opacity-30",
      )}
      onMouseEnter={play}
      onMouseLeave={stop}
      onFocus={play}
      onBlur={stop}
      tabIndex={0}
    >
      <div className="relative bg-stone-900 aspect-video">
        {source?.clip ? (
          <video
            ref={video}
            className="size-full object-contain"
            src={projectFileUrl(projectId, source.clip)}
            poster={poster ? projectFileUrl(projectId, poster) : undefined}
            muted
            loop
            playsInline
            preload="none"
          />
        ) : null}
        <span className="absolute top-2 left-2 px-1.5 py-0.5 font-mono text-xs text-white bg-black/60 rounded">
          {shot.shot_id}
        </span>
        {seconds && (
          <span className="absolute right-2 bottom-2 px-1.5 py-0.5 font-mono text-xs text-white bg-black/60 rounded">
            {t("shot.seconds", { seconds })}
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
          <span className={clsx(TAG, "ml-auto text-brand-700 bg-brand-50")} title={shot.edit.treatment_reason ?? undefined}>
            {labelFor(t, "treatment", shot.edit.treatment)}
          </span>
        </div>
      </div>
    </article>
  );
}
