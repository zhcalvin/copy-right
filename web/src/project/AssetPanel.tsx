import clsx from "clsx";

import { projectFileUrl } from "../api/client";
import type { Storyboard } from "../api/types";
import { useI18n } from "../i18n";
import { BTN_LINK, CARD } from "../lib/ui";
import type { AssetRef } from "./AssetEditor";

export type Filter = { kind: "character" | "scene"; id: string } | null;

interface AssetPanelProps {
  projectId: string;
  storyboard: Storyboard;
  filter: Filter;
  matchCount: number;
  onFilter: (filter: Filter) => void;
  onEdit: (asset: AssetRef) => void;
}

interface Row {
  id: string;
  label: string;
  hint: string;
  image: string | null;
  changed: boolean;
  asset: AssetRef;
}

/** A keyframe from the first shot that shows this character or scene. */
function keyframeFor(storyboard: Storyboard, match: (shot: Storyboard["shots"][number]) => boolean): string | null {
  const shot = storyboard.shots.find((s) => match(s) && s.source?.keyframes.length);
  const frames = shot?.source?.keyframes ?? [];
  return frames[Math.floor(frames.length / 2)] ?? null;
}

export function AssetPanel({ projectId, storyboard, filter, matchCount, onFilter, onEdit }: AssetPanelProps) {
  const { t } = useI18n();
  const characters: Row[] = storyboard.characters.map((c) => ({
    id: c.character_id,
    label: c.name,
    hint: c.description,
    image: c.reference_images[0] ?? keyframeFor(storyboard, (s) => s.assets.character_ids.includes(c.character_id)),
    changed: c.reference_images.length > 0,
    asset: { kind: "character", item: c },
  }));
  const scenes: Row[] = storyboard.scenes.map((s) => ({
    id: s.scene_id,
    label: s.description || s.scene_id,
    hint: s.scene_id,
    image: s.reference_images[0] ?? keyframeFor(storyboard, (shot) => shot.assets.scene_id === s.scene_id),
    changed: s.reference_images.length > 0,
    asset: { kind: "scene", item: s },
  }));
  const toggle = (kind: "character" | "scene", id: string) =>
    onFilter(filter?.kind === kind && filter.id === id ? null : { kind, id });

  return (
    <aside className={clsx(CARD, "shrink-0 gap-3 lg:sticky lg:top-4 lg:w-72 lg:max-h-screen lg:overflow-y-auto")}>
      <p className="text-xs text-stone-500">{t("assets.hint")}</p>
      <Section
        title={t("board.characters")}
        rows={characters}
        active={filter?.kind === "character" ? filter.id : null}
        projectId={projectId}
        onPick={(id) => toggle("character", id)}
        onEdit={onEdit}
      />
      <Section
        title={t("board.scenes")}
        rows={scenes}
        active={filter?.kind === "scene" ? filter.id : null}
        projectId={projectId}
        onPick={(id) => toggle("scene", id)}
        onEdit={onEdit}
      />
      {filter && (
        <div className="flex items-center justify-between text-xs text-stone-500">
          {t("board.filtered", { count: matchCount })}
          <button type="button" className={BTN_LINK} onClick={() => onFilter(null)}>
            {t("board.showAll")}
          </button>
        </div>
      )}
    </aside>
  );
}

interface SectionProps {
  title: string;
  rows: Row[];
  active: string | null;
  projectId: string;
  onPick: (id: string) => void;
  onEdit: (asset: AssetRef) => void;
}

function Section({ title, rows, active, projectId, onPick, onEdit }: SectionProps) {
  const { t } = useI18n();
  if (rows.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-xs font-semibold tracking-wide text-stone-500 uppercase">{title}</h3>
      <ul className="flex flex-col gap-1">
        {rows.map((row) => (
          <li
            key={row.id}
            className={clsx(
              "flex items-center gap-2 p-1.5 rounded-lg border transition-colors",
              active === row.id ? "bg-brand-50 border-brand-500" : "border-transparent hover:bg-stone-50",
            )}
          >
            <button
              type="button"
              title={row.hint}
              className="flex flex-1 items-center gap-2 min-w-0 text-left"
              onClick={() => onPick(row.id)}
            >
              <span className="relative shrink-0 overflow-hidden size-9 bg-stone-200 rounded-md">
                {row.image && <img src={projectFileUrl(projectId, row.image)} alt="" className="size-full object-cover" />}
                {row.changed && <span className="absolute right-0.5 bottom-0.5 size-2 bg-amber-500 rounded-full ring-2 ring-white" />}
              </span>
              <span className="text-sm text-stone-700 truncate">{row.label}</span>
            </button>
            <button
              type="button"
              className="shrink-0 px-2 py-1 text-xs text-brand-700 rounded-md hover:bg-brand-100"
              onClick={() => onEdit(row.asset)}
            >
              {t("assets.edit")}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
