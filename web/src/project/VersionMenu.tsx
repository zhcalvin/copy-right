import clsx from "clsx";
import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { Storyboard, VersionInfo } from "../api/types";
import { useI18n } from "../i18n";

interface VersionMenuProps {
  projectId: string;
  storyboard: Storyboard;
  onRestored: (storyboard: Storyboard) => void;
}

/** "Version N ▾": the history of saved versions, each restorable as a new version. */
export function VersionMenu({ projectId, storyboard, onRestored }: VersionMenuProps) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const [versions, setVersions] = useState<VersionInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .versions(projectId)
      .then((list) => !cancelled && setVersions(list))
      .catch((err: Error) => !cancelled && setError(err.message));
    const close = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => {
      cancelled = true;
      window.removeEventListener("mousedown", close);
    };
  }, [open, projectId, storyboard.version]);

  const restore = async (version: number) => {
    try {
      onRestored(await api.restoreVersion(projectId, version));
      setOpen(false);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const time = (iso: string) =>
    new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).toLocaleString(lang, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        className="px-2 py-0.5 text-sm text-stone-600 rounded-md border border-stone-200 hover:border-brand-500"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {t("board.version", { version: storyboard.version })} ▾
      </button>
      {open && (
        <div className="absolute left-0 z-20 flex flex-col mt-1 w-80 max-h-96 overflow-y-auto bg-white rounded-xl border border-stone-200 shadow-lg">
          <p className="px-3 py-2 text-xs text-stone-500 border-b border-stone-100">{t("versions.hint")}</p>
          {error && <p className="px-3 py-2 text-xs text-red-700">{error}</p>}
          {versions === null && !error && <p className="px-3 py-2 text-sm text-stone-500">{t("app.loading")}</p>}
          {versions?.map((v) => (
            <div key={v.version} className="flex items-center gap-2 px-3 py-2 text-sm border-b border-stone-50 last:border-b-0">
              <div className="flex flex-col flex-1 min-w-0">
                <span className="font-medium">
                  v{v.version} · <span className="font-normal text-stone-500">{t(`versions.${v.source}`)}</span>
                </span>
                <span className="text-xs text-stone-500 truncate" title={v.note ?? undefined}>
                  {time(v.created_at)}
                  {v.note ? ` · ${v.note}` : ""}
                </span>
              </div>
              {v.version === storyboard.version ? (
                <span className="shrink-0 text-xs text-brand-700">{t("versions.current")}</span>
              ) : (
                <button
                  type="button"
                  className={clsx("shrink-0 px-2 py-1 text-xs text-brand-700 rounded-md hover:bg-brand-50")}
                  onClick={() => restore(v.version)}
                >
                  {t("versions.restore")}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
