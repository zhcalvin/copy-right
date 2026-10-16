import clsx from "clsx";
import { type ReactNode, useState } from "react";

import { api, projectFileUrl } from "../api/client";
import type { Shot, Storyboard, Treatment } from "../api/types";
import { labelFor, useI18n } from "../i18n";
import { BTN_PRIMARY, BTN_SECONDARY, ERROR_BOX, INPUT } from "../lib/ui";
import { CAMERA_MOTIONS, SHOT_SIZES, TREATMENTS, replaceShot, toggleCharacter } from "./edits";
import { formatTimecode } from "./format";
import { Modal } from "./Modal";

interface ShotEditorProps {
  projectId: string;
  storyboard: Storyboard;
  shot: Shot;
  onClose: () => void;
  onSaved: (storyboard: Storyboard) => void;
}

const AUTO = "auto";
// INPUT without w-full, for a fixed-width select next to a growing input.
const SELECT_SM =
  "px-2 py-2 text-sm bg-white border border-stone-200 rounded-lg focus:outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100";

/** The fixed options, plus the current value if the model used another one. */
function withCurrent(options: readonly string[], current: string | null): string[] {
  return current && !options.includes(current) ? [...options, current] : [...options];
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium text-stone-700">{label}</span>
      {children}
    </label>
  );
}

export function ShotEditor({ projectId, storyboard, shot: initial, onClose, onSaved }: ShotEditorProps) {
  const { t } = useI18n();
  const [shot, setShot] = useState<Shot>(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const source = shot.source;
  const keyframes = source?.keyframes ?? [];

  const update = (patch: (current: Shot) => Shot) => setShot((current) => patch(current));
  const narrative = (patch: Partial<Shot["narrative"]>) =>
    update((s) => ({ ...s, narrative: { ...s.narrative, ...patch } }));
  const visual = (patch: Partial<Shot["visual"]>) => update((s) => ({ ...s, visual: { ...s.visual, ...patch } }));

  const treatmentChoice = shot.edit.treatment_locked ? shot.edit.treatment : AUTO;
  const chooseTreatment = (value: string) =>
    update((s) => ({
      ...s,
      edit:
        value === AUTO
          ? { ...s.edit, treatment_locked: false, treatment: s.edit.treatment === "removed" ? "keep" : s.edit.treatment }
          : {
              ...s.edit,
              treatment_locked: true,
              treatment: value as Treatment,
              treatment_reason: t("editor.lockedReason"),
              trim:
                value === "trim" && source && !s.edit.trim
                  ? { keep_start: source.start, keep_end: Number(((source.start + source.end) / 2).toFixed(2)) }
                  : s.edit.trim,
            },
    }));

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveStoryboard(projectId, replaceShot(storyboard, shot), t("editor.note", { shot: shot.shot_id }));
      onSaved(saved.storyboard);
      onClose();
    } catch (err) {
      setError((err as Error).message);
      setSaving(false);
    }
  };

  const removed = shot.edit.treatment === "removed";
  return (
    <Modal
      wide
      title={
        <span className="flex items-baseline gap-2">
          <span className="font-mono">{shot.shot_id}</span>
          {source && (
            <span className="font-mono text-sm font-normal text-stone-500">
              {formatTimecode(source.start)}–{formatTimecode(source.end)}
            </span>
          )}
        </span>
      }
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            className={clsx(BTN_SECONDARY, removed ? "" : "text-red-700")}
            onClick={() => chooseTreatment(removed ? AUTO : "removed")}
          >
            {removed ? t("editor.restore") : t("editor.remove")}
          </button>
          {error && <span className="text-sm text-red-700">{error}</span>}
          <button type="button" className={clsx(BTN_SECONDARY, "ml-auto")} onClick={onClose}>
            {t("upload.cancel")}
          </button>
          <button type="button" className={BTN_PRIMARY} disabled={saving} onClick={save}>
            {saving ? t("setup.saving") : t("editor.save")}
          </button>
        </>
      }
    >
      <div className="grid gap-5 md:grid-cols-2">
        <div className="flex flex-col gap-3">
          {source?.clip ? (
            <video
              className="w-full bg-stone-900 rounded-lg aspect-video"
              src={projectFileUrl(projectId, source.clip)}
              poster={keyframes[0] ? projectFileUrl(projectId, keyframes[0]) : undefined}
              controls
              loop
              autoPlay
              muted
              playsInline
            />
          ) : (
            <div className="flex items-center justify-center p-6 text-sm text-center text-stone-500 bg-stone-100 rounded-lg aspect-video">
              {t("editor.newShot")}
            </div>
          )}
          <div className="grid grid-cols-3 gap-2">
            {keyframes.map((frame) => (
              <img key={frame} src={projectFileUrl(projectId, frame)} alt="" className="w-full rounded aspect-video object-cover" />
            ))}
          </div>
          <Field label={t("editor.treatment")}>
            <select className={INPUT} value={treatmentChoice} onChange={(event) => chooseTreatment(event.target.value)}>
              <option value={AUTO}>
                {t("editor.auto", { treatment: labelFor(t, "treatment", initial.edit.treatment) })}
              </option>
              {TREATMENTS.map((treatment) => (
                <option key={treatment} value={treatment}>
                  {labelFor(t, "treatment", treatment)}
                </option>
              ))}
            </select>
          </Field>
          <p className="text-xs text-stone-500">{t("editor.treatmentHint")}</p>
          {shot.edit.treatment === "trim" && shot.edit.trim && source && (
            <Field label={t("editor.keepUntil")}>
              <input
                type="number"
                className={INPUT}
                step={0.1}
                min={0.2}
                max={Number((source.end - source.start).toFixed(2))}
                value={Number((shot.edit.trim.keep_end - source.start).toFixed(2))}
                onChange={(event) =>
                  update((s) => ({
                    ...s,
                    edit: {
                      ...s.edit,
                      trim: { keep_start: source.start, keep_end: source.start + Number(event.target.value) },
                    },
                  }))
                }
              />
            </Field>
          )}
          {!source && (
            <Field label={t("editor.duration")}>
              <input
                type="number"
                className={INPUT}
                min={1}
                max={15}
                step={0.5}
                value={shot.generation.duration ?? 4}
                onChange={(event) =>
                  update((s) => ({ ...s, generation: { ...s.generation, duration: Number(event.target.value) } }))
                }
              />
            </Field>
          )}
        </div>

        <div className={clsx("flex flex-col gap-4", removed && "opacity-50")}>
          <Field label={t("editor.description")}>
            <textarea
              className={clsx(INPUT, "min-h-24")}
              value={shot.narrative.description}
              onChange={(event) => narrative({ description: event.target.value })}
            />
          </Field>

          <div className="flex flex-col gap-2 text-sm">
            <span className="font-medium text-stone-700">{t("board.characters")}</span>
            <div className="flex flex-wrap gap-1.5">
              {storyboard.characters.map((character) => {
                const on = shot.assets.character_ids.includes(character.character_id);
                return (
                  <button
                    key={character.character_id}
                    type="button"
                    className={clsx(
                      "px-2.5 py-1 text-xs rounded-full border transition-colors",
                      on ? "text-white bg-brand-600 border-brand-600" : "text-stone-600 bg-white border-stone-200 hover:border-brand-500",
                    )}
                    onClick={() => update((s) => toggleCharacter(s, character.character_id))}
                  >
                    {character.name}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex flex-col gap-2 text-sm">
            <span className="font-medium text-stone-700">{t("shot.dialogue")}</span>
            {shot.narrative.dialogue.map((line, index) => (
              <div key={index} className="flex gap-2">
                <select
                  className={clsx(SELECT_SM, "shrink-0 w-36")}
                  aria-label={t("editor.speaker")}
                  value={line.character_id ?? ""}
                  onChange={(event) =>
                    narrative({
                      dialogue: shot.narrative.dialogue.map((d, i) =>
                        i === index ? { ...d, character_id: event.target.value || null } : d,
                      ),
                    })
                  }
                >
                  <option value="">{t("editor.noSpeaker")}</option>
                  {storyboard.characters.map((c) => (
                    <option key={c.character_id} value={c.character_id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <input
                  className={clsx(INPUT, "flex-1 min-w-0")}
                  value={line.text}
                  onChange={(event) =>
                    narrative({
                      dialogue: shot.narrative.dialogue.map((d, i) => (i === index ? { ...d, text: event.target.value } : d)),
                    })
                  }
                />
                <button
                  type="button"
                  aria-label={t("editor.removeLine")}
                  className="shrink-0 px-2 text-stone-400 hover:text-red-600"
                  onClick={() => narrative({ dialogue: shot.narrative.dialogue.filter((_, i) => i !== index) })}
                >
                  ×
                </button>
              </div>
            ))}
            <button
              type="button"
              className="self-start text-sm text-brand-700 hover:underline"
              onClick={() =>
                narrative({
                  dialogue: [...shot.narrative.dialogue, { character_id: null, text: "", start: null, end: null }],
                })
              }
            >
              + {t("editor.addLine")}
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("editor.shotSize")}>
              <select
                className={INPUT}
                value={shot.visual.shot_size ?? ""}
                onChange={(event) => visual({ shot_size: event.target.value || null })}
              >
                <option value="">—</option>
                {withCurrent(SHOT_SIZES, shot.visual.shot_size).map((size) => (
                  <option key={size} value={size}>
                    {labelFor(t, "size", size)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("editor.camera")}>
              <select
                className={INPUT}
                value={shot.visual.camera_motion ?? ""}
                onChange={(event) => visual({ camera_motion: event.target.value || null })}
              >
                <option value="">—</option>
                {withCurrent(CAMERA_MOTIONS, shot.visual.camera_motion).map((motion) => (
                  <option key={motion} value={motion}>
                    {labelFor(t, "motion", motion)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("editor.composition")}>
              <input
                className={INPUT}
                value={shot.visual.composition ?? ""}
                onChange={(event) => visual({ composition: event.target.value || null })}
              />
            </Field>
            <Field label={t("editor.lighting")}>
              <input
                className={INPUT}
                value={shot.visual.lighting ?? ""}
                onChange={(event) => visual({ lighting: event.target.value || null })}
              />
            </Field>
          </div>

          {storyboard.scenes.length > 0 && (
            <Field label={t("editor.scene")}>
              <select
                className={INPUT}
                value={shot.assets.scene_id ?? ""}
                onChange={(event) =>
                  update((s) => ({ ...s, assets: { ...s.assets, scene_id: event.target.value || null } }))
                }
              >
                <option value="">—</option>
                {storyboard.scenes.map((scene) => (
                  <option key={scene.scene_id} value={scene.scene_id}>
                    {scene.description || scene.scene_id}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {error && <div className={ERROR_BOX}>{error}</div>}
        </div>
      </div>
    </Modal>
  );
}
