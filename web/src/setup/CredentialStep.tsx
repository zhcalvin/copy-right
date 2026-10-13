import type { CredentialView, ProviderSpec, StorageSpec } from "../api/types";
import { localized, useI18n } from "../i18n";
import { CredentialForm, type Draft } from "./CredentialForm";
import { StorageSection } from "./StorageSection";

interface CredentialStepProps {
  providers: ProviderSpec[];
  storage: StorageSpec[];
  storageId: string;
  onStorageChange: (id: string) => void;
  credentials: Record<string, CredentialView>;
  drafts: Record<string, Draft>;
  onDraftChange: (owner: string, draft: Draft) => void;
  showStorage: boolean;
}

export function CredentialStep({ providers, showStorage, drafts, onDraftChange, credentials, ...storage }: CredentialStepProps) {
  const { t, lang } = useI18n();
  return (
    <div className="flex flex-col gap-6">
      {providers.length === 0 ? (
        <p className="text-sm text-stone-500">{t("cred.none")}</p>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-xs text-stone-500">{t("cred.hint")}</p>
          {providers.map((p) => (
            <CredentialForm
              key={p.id}
              title={localized(p, "name", lang)}
              docs={p.docs}
              specs={p.credentials}
              view={credentials[p.id]}
              draft={drafts[p.id] ?? {}}
              onChange={(draft) => onDraftChange(p.id, draft)}
            />
          ))}
        </div>
      )}
      {showStorage && (
        <StorageSection
          storage={storage.storage}
          value={storage.storageId}
          onChange={storage.onStorageChange}
          credentials={credentials}
          drafts={drafts}
          onDraftChange={onDraftChange}
        />
      )}
    </div>
  );
}
