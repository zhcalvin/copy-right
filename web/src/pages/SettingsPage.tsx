import { useState } from "react";

import { api } from "../api/client";
import { localized, useI18n } from "../i18n";
import { BTN_LINK, BTN_PRIMARY, CARD, ERROR_BOX } from "../lib/ui";
import { navigate } from "../router";
import { CapabilityTable } from "../setup/CapabilityTable";
import { CredentialCard } from "../setup/CredentialCard";
import { type Selections, fromSaved, missingRequired, providersInUse, toList } from "../setup/selections";
import { storageOwner } from "../setup/StorageSection";
import { type SetupData, useSetupData } from "../setup/useSetupData";

export function SettingsPage() {
  const { t } = useI18n();
  const { state, updateCredential } = useSetupData();
  if (state.kind === "loading") return <p className="text-sm text-stone-500">{t("app.loading")}</p>;
  if (state.kind === "error") return <div className={ERROR_BOX}>{state.message}</div>;
  return <Settings data={state.data} onCredentialSaved={updateCredential} />;
}

interface SettingsProps {
  data: SetupData;
  onCredentialSaved: Parameters<typeof CredentialCard>[0]["onSaved"];
}

function Settings({ data, onCredentialSaved }: SettingsProps) {
  const { t, lang } = useI18n();
  const { capabilities, providers } = data;
  const [selections, setSelections] = useState<Selections | null>(() => fromSaved(data.settings.capabilities));
  const [storageId, setStorageId] = useState(data.settings.storage);
  const [showAll, setShowAll] = useState(false);
  const [status, setStatus] = useState<{ kind: "idle" | "saving" | "saved" } | { kind: "error"; message: string }>({
    kind: "idle",
  });

  if (!selections) {
    return (
      <div className={CARD}>
        <p className="text-sm">{t("settings.notSetUp")}</p>
        <button type="button" className={BTN_PRIMARY} onClick={() => navigate("/setup")}>
          {t("settings.start")}
        </button>
      </div>
    );
  }

  const inUse = new Set(providersInUse(selections, providers).map((p) => p.id));
  const listed = Object.values(providers).filter((p) => p.credentials.length > 0 && (showAll || inUse.has(p.id)));
  const storage = data.storage.find((s) => s.id === storageId);
  const missing = missingRequired(selections, capabilities);

  const save = async () => {
    setStatus({ kind: "saving" });
    try {
      await api.saveCapabilities(toList(selections, capabilities));
      await api.saveStorage(storageId);
      setStatus({ kind: "saved" });
    } catch (e) {
      setStatus({ kind: "error", message: (e as Error).message });
    }
  };

  return (
    <div className="flex flex-col gap-8">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold">{t("settings.title")}</h1>
        <button type="button" className={`${BTN_LINK} ml-auto`} onClick={() => navigate("/setup")}>
          {t("settings.rerun")}
        </button>
      </div>

      {data.settings.key_storage === "file" && (
        <p className="p-3 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg">
          {t("settings.keyFile")}
        </p>
      )}

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">{t("settings.capabilities")}</h2>
        <CapabilityTable
          capabilities={capabilities}
          providers={providers}
          selections={selections}
          region={null}
          onChange={setSelections}
        />
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-stone-600">{t("settings.upload")}</span>
          <select
            className="px-3 py-2 text-sm bg-white border border-stone-200 rounded-lg"
            value={storageId}
            onChange={(e) => setStorageId(e.target.value)}
          >
            {data.storage.map((s) => (
              <option key={s.id} value={s.id}>
                {localized(s, "name", lang)}
              </option>
            ))}
          </select>
          {status.kind === "saved" && <span className="text-sm text-brand-700">{t("settings.saved")}</span>}
          {status.kind === "error" && <span className="text-sm text-red-600">{status.message}</span>}
          <button
            type="button"
            className={`${BTN_PRIMARY} ml-auto`}
            disabled={status.kind === "saving" || missing.length > 0}
            onClick={() => void save()}
          >
            {status.kind === "saving" ? t("setup.saving") : t("settings.save")}
          </button>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold">{t("settings.keys")}</h2>
          <button type="button" className={`${BTN_LINK} ml-auto`} onClick={() => setShowAll(!showAll)}>
            {showAll ? t("settings.showInUse") : t("settings.showAll")}
          </button>
        </div>
        {listed.length === 0 && <p className="text-sm text-stone-500">{t("cred.none")}</p>}
        {listed.map((p) => (
          <CredentialCard
            key={p.id}
            owner={p.id}
            title={localized(p, "name", lang)}
            docs={p.docs}
            specs={p.credentials}
            view={data.credentials[p.id]}
            testable
            onSaved={onCredentialSaved}
          />
        ))}
        {storage && storage.credentials.length > 0 && (
          <CredentialCard
            owner={storageOwner(storage.id)}
            title={t("settings.uploadCard", { name: localized(storage, "name", lang) })}
            docs={storage.docs}
            specs={storage.credentials}
            view={data.credentials[storageOwner(storage.id)]}
            testable={false}
            onSaved={onCredentialSaved}
          />
        )}
      </section>
    </div>
  );
}
