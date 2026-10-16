import { useCallback, useMemo, useState } from "react";

import type { Shot, Storyboard, Treatment } from "../api/types";
import { labelFor, useI18n } from "../i18n";
import { TAG } from "../lib/ui";
import { AssetEditor, type AssetRef } from "./AssetEditor";
import { AssetPanel, type Filter } from "./AssetPanel";
import { PlotEditPanel } from "./PlotEditPanel";
import { ShotCard, TREATMENT_TAG } from "./ShotCard";
import { ShotEditor } from "./ShotEditor";
import { VersionMenu } from "./VersionMenu";

interface StoryboardBoardProps {
  projectId: string;
  storyboard: Storyboard;
  onChange: (storyboard: Storyboard) => void;
  /** Generated clips by shot id, shown instead of the source once a render finishes. */
  results?: Record<string, string>;
}

export function StoryboardBoard({ projectId, storyboard, onChange, results }: StoryboardBoardProps) {
  const { t } = useI18n();
  const [filter, setFilter] = useState<Filter>(null);
  const [editing, setEditing] = useState<Shot | null>(null);
  const [asset, setAsset] = useState<AssetRef | null>(null);
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const onPreview = useCallback((ids: string[]) => setHighlight(new Set(ids)), []);
  const characters = useMemo(
    () => new Map(storyboard.characters.map((c) => [c.character_id, c])),
    [storyboard.characters],
  );

  const matches = (shot: Shot): boolean => {
    if (!filter) return true;
    return filter.kind === "character" ? shot.assets.character_ids.includes(filter.id) : shot.assets.scene_id === filter.id;
  };
  const matchCount = storyboard.shots.filter(matches).length;
  const counts = storyboard.shots.reduce<Partial<Record<Treatment, number>>>(
    (acc, shot) => ({ ...acc, [shot.edit.treatment]: (acc[shot.edit.treatment] ?? 0) + 1 }),
    {},
  );

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="text-lg font-semibold">{t("board.title")}</h2>
        <VersionMenu projectId={projectId} storyboard={storyboard} onRestored={onChange} />
        <span className="text-sm text-stone-500">
          {t("board.summary", {
            shots: storyboard.shots.length,
            characters: storyboard.characters.length,
            scenes: storyboard.scenes.length,
          })}
        </span>
        <span className="flex flex-wrap gap-1">
          {(Object.entries(counts) as [Treatment, number][])
            .filter(([treatment]) => treatment !== "keep")
            .map(([treatment, count]) => (
              <span key={treatment} className={`${TAG} ${TREATMENT_TAG[treatment]}`}>
                {labelFor(t, "treatment", treatment)} × {count}
              </span>
            ))}
        </span>
        <span className="ml-auto text-xs text-stone-400">{t("board.hint")}</span>
      </div>

      <PlotEditPanel projectId={projectId} onApplied={onChange} onPreview={onPreview} />

      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <AssetPanel
          projectId={projectId}
          storyboard={storyboard}
          filter={filter}
          matchCount={matchCount}
          onFilter={setFilter}
          onEdit={setAsset}
        />
        <div className="grid flex-1 gap-4 min-w-0 sm:grid-cols-2 xl:grid-cols-3">
          {storyboard.shots.map((shot) => (
            <ShotCard
              key={shot.shot_id}
              projectId={projectId}
              shot={shot}
              characters={characters}
              dimmed={!matches(shot)}
              highlighted={highlight.has(shot.shot_id)}
              resultClip={results?.[shot.shot_id]}
              onOpen={() => setEditing(shot)}
            />
          ))}
        </div>
      </div>

      {editing && (
        <ShotEditor
          projectId={projectId}
          storyboard={storyboard}
          shot={editing}
          onClose={() => setEditing(null)}
          onSaved={onChange}
        />
      )}
      {asset && (
        <AssetEditor
          projectId={projectId}
          storyboard={storyboard}
          asset={asset}
          onClose={() => setAsset(null)}
          onSaved={onChange}
        />
      )}
    </section>
  );
}
