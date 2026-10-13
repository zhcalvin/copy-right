import clsx from "clsx";

import type { CredentialView, StorageSpec } from "../api/types";
import { localized, useI18n } from "../i18n";
import { CredentialForm, type Draft } from "./CredentialForm";

export const storageOwner = (id: string) => `storage:${id}`;

interface StorageSectionProps {
  storage: StorageSpec[];
  value: string;
  onChange: (id: string) => void;
  credentials: Record<string, CredentialView>;
  drafts: Record<string, Draft>;
  onDraftChange: (owner: string, draft: Draft) => void;
}

export function StorageSection({ storage, value, onChange, credentials, drafts, onDraftChange }: StorageSectionProps) {
  const { t, lang } = useI18n();
  const selected = storage.find((s) => s.id === value);
  const owner = storageOwner(value);

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col">
        <h3 className="text-sm font-semibold">{t("storage.title")}</h3>
        <p className="text-xs text-stone-500">{t("storage.hint")}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {storage.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => onChange(s.id)}
            className={clsx(
              "px-3 py-1.5 text-sm border rounded-lg transition-colors",
              s.id === value
                ? "text-brand-700 bg-brand-50 border-brand-500"
                : "text-stone-600 bg-white border-stone-200 hover:border-brand-500",
            )}
          >
            {localized(s, "name", lang)}
          </button>
        ))}
      </div>
      {selected && <p className="text-xs text-stone-500">{localized(selected, "description", lang)}</p>}
      {selected && selected.credentials.length > 0 && (
        <CredentialForm
          title={localized(selected, "name", lang)}
          docs={selected.docs}
          specs={selected.credentials}
          view={credentials[owner]}
          draft={drafts[owner] ?? {}}
          onChange={(draft) => onDraftChange(owner, draft)}
        />
      )}
    </section>
  );
}
