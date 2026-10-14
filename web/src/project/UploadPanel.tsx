import { useState } from "react";

import { api } from "../api/client";
import type { Project } from "../api/types";
import { useI18n } from "../i18n";
import { BTN_PRIMARY, BTN_SECONDARY, CARD, ERROR_BOX } from "../lib/ui";

const ACCEPT = ".mp4,.mov,.m4v,.mkv,.webm,.avi,video/*";

interface UploadPanelProps {
  project: Project;
  onUploaded: (project: Project) => void;
  onCancel?: () => void;
}

export function UploadPanel({ project, onUploaded, onCancel }: UploadPanelProps) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [rights, setRights] = useState(false);
  const [status, setStatus] = useState<{ kind: "idle" | "uploading" } | { kind: "error"; message: string }>({
    kind: "idle",
  });

  const submit = async () => {
    if (!file) return;
    setStatus({ kind: "uploading" });
    try {
      onUploaded(await api.uploadVideo(project.id, file, rights));
    } catch (error) {
      setStatus({ kind: "error", message: (error as Error).message });
    }
  };

  const uploading = status.kind === "uploading";
  return (
    <section className={CARD}>
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold">{t("upload.title")}</h2>
        <p className="text-sm text-stone-500">{t("upload.hint")}</p>
      </div>
      <label className="flex flex-col items-center gap-2 p-8 text-sm text-center text-stone-600 bg-brand-50/40 border border-dashed border-brand-100 rounded-xl cursor-pointer hover:bg-brand-50">
        <span className="font-medium text-brand-700">{file ? file.name : t("upload.choose")}</span>
        {file && <span className="text-xs text-stone-500">{(file.size / 1024 / 1024).toFixed(1)} MB</span>}
        <input
          type="file"
          accept={ACCEPT}
          className="sr-only"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <label className="flex items-start gap-2 text-sm text-stone-700">
        <input
          type="checkbox"
          className="shrink-0 mt-0.5 size-4 accent-brand-600"
          checked={rights}
          onChange={(event) => setRights(event.target.checked)}
        />
        {t("upload.rights")}
      </label>
      {status.kind === "error" && <div className={ERROR_BOX}>{status.message}</div>}
      <div className="flex gap-2">
        <button type="button" className={BTN_PRIMARY} disabled={!file || !rights || uploading} onClick={submit}>
          {uploading ? t("upload.uploading") : t("upload.submit")}
        </button>
        {onCancel && (
          <button type="button" className={BTN_SECONDARY} disabled={uploading} onClick={onCancel}>
            {t("upload.cancel")}
          </button>
        )}
      </div>
    </section>
  );
}
