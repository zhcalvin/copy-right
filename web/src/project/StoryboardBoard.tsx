import clsx from "clsx";
import { useMemo, useState } from "react";

import type { Storyboard } from "../api/types";
import { useI18n } from "../i18n";
import { BTN_LINK, CARD } from "../lib/ui";
import { ShotCard } from "./ShotCard";

type Filter = { kind: "character" | "scene"; id: string } | null;

const CHIP = "px-2.5 py-1 text-xs text-left rounded-full border transition-colors";

export function StoryboardBoard({ projectId, storyboard }: { projectId: string; storyboard: Storyboard }) {
  const { t } = useI18n();
  const [filter, setFilter] = useState<Filter>(null);
  const characters = useMemo(
    () => new Map(storyboard.characters.map((c) => [c.character_id, c])),
    [storyboard.characters],
  );

  const matches = (shotIndex: number): boolean => {
    if (!filter) return true;
    const assets = storyboard.shots[shotIndex].assets;
    return filter.kind === "character" ? assets.character_ids.includes(filter.id) : assets.scene_id === filter.id;
  };
  const matchCount = storyboard.shots.filter((_, index) => matches(index)).length;
  const toggle = (next: NonNullable<Filter>) =>
    setFilter((current) => (current?.kind === next.kind && current.id === next.id ? null : next));

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-semibold">{t("board.title")}</h2>
        <span className="text-sm text-stone-500">
          {t("board.version", { version: storyboard.version })} ·{" "}
          {t("board.summary", {
            shots: storyboard.shots.length,
            characters: storyboard.characters.length,
            scenes: storyboard.scenes.length,
          })}
        </span>
        <span className="ml-auto text-xs text-stone-400">{t("board.hint")}</span>
      </div>

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <aside className={clsx(CARD, "shrink-0 gap-3 lg:sticky lg:top-4 lg:w-64")}>
          <ElementList
            title={t("board.characters")}
            items={storyboard.characters.map((c) => ({ id: c.character_id, label: c.name, hint: c.description }))}
            active={filter?.kind === "character" ? filter.id : null}
            onPick={(id) => toggle({ kind: "character", id })}
          />
          <ElementList
            title={t("board.scenes")}
            items={storyboard.scenes.map((s) => ({ id: s.scene_id, label: s.description || s.scene_id, hint: s.scene_id }))}
            active={filter?.kind === "scene" ? filter.id : null}
            onPick={(id) => toggle({ kind: "scene", id })}
          />
          {filter && (
            <div className="flex items-center justify-between text-xs text-stone-500">
              {t("board.filtered", { count: matchCount })}
              <button type="button" className={BTN_LINK} onClick={() => setFilter(null)}>
                {t("board.showAll")}
              </button>
            </div>
          )}
        </aside>

        <div className="grid flex-1 gap-4 min-w-0 sm:grid-cols-2 xl:grid-cols-3">
          {storyboard.shots.map((shot, index) => (
            <ShotCard
              key={shot.shot_id}
              projectId={projectId}
              shot={shot}
              characters={characters}
              dimmed={!matches(index)}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

interface ElementListProps {
  title: string;
  items: { id: string; label: string; hint: string }[];
  active: string | null;
  onPick: (id: string) => void;
}

function ElementList({ title, items, active, onPick }: ElementListProps) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold tracking-wide text-stone-500 uppercase">{title}</h3>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            title={item.hint}
            className={clsx(
              CHIP,
              active === item.id
                ? "text-white bg-brand-600 border-brand-600"
                : "text-stone-700 bg-white border-stone-200 hover:border-brand-500",
            )}
            onClick={() => onPick(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}
