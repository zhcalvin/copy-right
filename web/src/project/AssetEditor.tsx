import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";

import { api, projectFileUrl } from "../api/client";
import type { Candidate, Character, Scene, Storyboard } from "../api/types";
import { labelFor, useI18n } from "../i18n";
import { BTN_PRIMARY, BTN_SECONDARY, ERROR_BOX, INPUT } from "../lib/ui";
import { replaceCharacter, replaceScene } from "./edits";
import { Modal } from "./Modal";

const POLL_MS = 2000;

export type AssetRef = { kind: "character"; item: Character } | { kind: "scene"; item: Scene };

interface AssetEditorProps {
  projectId: string;
  storyboard: Storyboard;
  asset: AssetRef;
  onClose: () => void;
  onSaved: (storyboard: Storyboard) => void;
}

export function assetId(asset: AssetRef): string {
  return asset.kind === "character" ? asset.item.character_id : asset.item.scene_id;
}

/** Candidates for one asset, polled while any is still being generated. */
function useCandidates(projectId: string, id: string) {
  const [items, setItems] = useState<Candidate[]>([]);
  const load = useCallback(async () => {
    const all = await api.candidates(projectId);
    setItems(all.filter((c) => c.asset_id === id));
  }, [projectId, id]);
  const busy = items.some((c) => c.status === "queued" || c.status === "running");

  useEffect(() => {
    void load().catch(() => undefined);
  }, [load]);
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => void load().catch(() => undefined), POLL_MS);
    return () => window.clearInterval(timer);
  }, [busy, load]);
  return { items, busy, reload: load };
}

export function AssetEditor({ projectId, storyboard, asset, onClose, onSaved }: AssetEditorProps) {
  const { t } = useI18n();
  const id = assetId(asset);
  const current =
    asset.kind === "character"
      ? storyboard.characters.find((c) => c.character_id === id)
      : storyboard.scenes.find((s) => s.scene_id === id);
  const [name, setName] = useState(asset.kind === "character" ? asset.item.name : "");
  const [description, setDescription] = useState(asset.item.description);
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const candidates = useCandidates(projectId, id);
  const images = current?.reference_images ?? [];

  const run = async (action: () => Promise<Storyboard | void>) => {
    setBusy(true);
    setError(null);
    try {
      const next = await action();
      if (next) onSaved(next);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const saveText = () =>
    run(async () => {
      const next =
        asset.kind === "character"
          ? replaceCharacter(storyboard, { ...(current as Character), name: name.trim() || id, description })
          : replaceScene(storyboard, { ...(current as Scene), description });
      const saved = await api.saveStoryboard(projectId, next, t("assets.note", { name: name || id }));
      onClose();
      return saved.storyboard;
    });

  const generate = () =>
    run(async () => {
      await api.generateReference(projectId, id, prompt.trim());
      setPrompt("");
      await candidates.reload();
    });

  const ready = candidates.items.filter((c) => c.status === "succeeded" && c.image && !images.includes(c.image));
  const pending = candidates.items.filter((c) => c.status === "queued" || c.status === "running");
  const failed = candidates.items.filter((c) => c.status === "failed").slice(0, 1);

  return (
    <Modal
      wide
      title={asset.kind === "character" ? t("assets.editCharacter") : t("assets.editScene")}
      onClose={onClose}
      footer={
        <>
          <span className="text-xs text-stone-500">{t("assets.swapHint")}</span>
          <button type="button" className={clsx(BTN_SECONDARY, "ml-auto")} onClick={onClose}>
            {t("upload.cancel")}
          </button>
          <button type="button" className={BTN_PRIMARY} disabled={busy} onClick={saveText}>
            {t("editor.save")}
          </button>
        </>
      }
    >
      <div className="grid gap-5 md:grid-cols-2">
        <div className="flex flex-col gap-3">
          {asset.kind === "character" && (
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-stone-700">{t("assets.name")}</span>
              <input className={INPUT} value={name} onChange={(event) => setName(event.target.value)} />
            </label>
          )}
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-stone-700">{t("assets.description")}</span>
            <textarea
              className={clsx(INPUT, "min-h-20")}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>

          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-medium text-stone-700">{t("assets.references")}</h3>
            {images.length === 0 && <p className="text-sm text-stone-500">{t("assets.noReferences")}</p>}
            <div className="grid grid-cols-3 gap-2">
              {images.map((image, index) => (
                <figure key={image} className="relative overflow-hidden bg-stone-100 rounded-lg aspect-square">
                  <img src={projectFileUrl(projectId, image)} alt="" className="size-full object-cover" />
                  {index === 0 && (
                    <figcaption className="absolute top-1 left-1 px-1.5 py-0.5 text-xs text-white bg-brand-600 rounded">
                      {t("assets.main")}
                    </figcaption>
                  )}
                  <button
                    type="button"
                    aria-label={t("assets.removeImage")}
                    className="absolute top-1 right-1 size-6 text-white bg-black/60 rounded-full hover:bg-red-600"
                    disabled={busy}
                    onClick={() => run(() => api.removeReference(projectId, id, image))}
                  >
                    ×
                  </button>
                </figure>
              ))}
            </div>
            <label className={clsx(BTN_SECONDARY, "self-start cursor-pointer")}>
              {t("assets.upload")}
              <input
                type="file"
                accept=".png,.jpg,.jpeg,.webp,image/*"
                className="sr-only"
                disabled={busy}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void run(() => api.uploadReference(projectId, id, file));
                }}
              />
            </label>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium text-stone-700">{t("assets.generate")}</h3>
          <p className="text-xs text-stone-500">{t("assets.generateHint")}</p>
          <textarea
            className={clsx(INPUT, "min-h-20")}
            placeholder={t("assets.promptPlaceholder")}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
          />
          <button type="button" className={clsx(BTN_PRIMARY, "self-start")} disabled={busy || !prompt.trim()} onClick={generate}>
            {t("assets.generateButton")}
          </button>
          {pending.length > 0 && (
            <p className="text-sm text-stone-500 animate-pulse">{t("assets.generating", { count: pending.length })}</p>
          )}
          {failed.map((c) => (
            <div key={c.job_id} className={ERROR_BOX}>
              {labelFor(t, "error", c.error_type ?? "provider_error")}: {c.error}
            </div>
          ))}
          {ready.length > 0 && (
            <div className="grid grid-cols-3 gap-2">
              {ready.map((c) => (
                <figure key={c.job_id} className="flex flex-col gap-1">
                  <img
                    src={projectFileUrl(projectId, c.image as string)}
                    alt={c.prompt}
                    title={c.prompt}
                    className="w-full bg-stone-100 rounded-lg aspect-square object-cover"
                  />
                  <button
                    type="button"
                    className="text-xs text-brand-700 hover:underline"
                    disabled={busy}
                    onClick={() => run(() => api.useCandidate(projectId, c.job_id))}
                  >
                    {t("assets.use")}
                  </button>
                </figure>
              ))}
            </div>
          )}
        </div>
      </div>
      {error && <div className={ERROR_BOX}>{error}</div>}
    </Modal>
  );
}
