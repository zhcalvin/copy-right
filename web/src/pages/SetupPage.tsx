import { useMemo, useState } from "react";

import { api } from "../api/client";
import { useI18n } from "../i18n";
import { navigate } from "../router";
import { BTN_PRIMARY, BTN_SECONDARY, ERROR_BOX } from "../lib/ui";
import { CapabilityTable } from "../setup/CapabilityTable";
import { ConnectionStep } from "../setup/ConnectionStep";
import { CredentialStep } from "../setup/CredentialStep";
import type { Draft } from "../setup/CredentialForm";
import { type PresetChoice, PresetStep } from "../setup/PresetStep";
import { saveDrafts } from "../setup/saveDrafts";
import {
  type Selections,
  fromPreset,
  fromSaved,
  missingRequired,
  providersInUse,
  selectedProviderIds,
  toList,
} from "../setup/selections";
import { STEPS, StepIndicator } from "../setup/StepIndicator";
import { storageOwner } from "../setup/StorageSection";
import { SummaryStep } from "../setup/SummaryStep";
import { type SetupData, useSetupData } from "../setup/useSetupData";

export function SetupPage({ onConfigured }: { onConfigured: () => void }) {
  const { t } = useI18n();
  const { state, updateCredential } = useSetupData();
  if (state.kind === "loading") return <p className="text-sm text-stone-500">{t("app.loading")}</p>;
  if (state.kind === "error") return <div className={ERROR_BOX}>{state.message}</div>;
  return <Wizard data={state.data} onSaved={(views) => views.forEach(updateCredential)} onConfigured={onConfigured} />;
}

interface WizardProps {
  data: SetupData;
  onSaved: (views: Awaited<ReturnType<typeof saveDrafts>>) => void;
  onConfigured: () => void;
}

function Wizard({ data, onSaved, onConfigured }: WizardProps) {
  const { t } = useI18n();
  const { capabilities, providers, presets } = data;
  const [step, setStep] = useState(0);
  const [choice, setChoice] = useState<PresetChoice | null>(null);
  const [selections, setSelections] = useState<Selections | null>(() => fromSaved(data.settings.capabilities));
  const [storageId, setStorageId] = useState(data.settings.storage);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inUse = useMemo(() => (selections ? providersInUse(selections, providers) : []), [selections, providers]);
  const testIds = useMemo(() => (selections ? selectedProviderIds(selections) : []), [selections]);
  const isDemo = choice === "demo";

  const pickPreset = (next: PresetChoice) => {
    setChoice(next);
    const base = presets.find((p) => p.id === (next === "custom" ? "cn" : next));
    if (base) setSelections(fromPreset(base, capabilities, providers));
  };

  const withBusy = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const saveCredentialStep = () =>
    withBusy(async () => {
      const owners = [...inUse.map((p) => p.id), ...(isDemo ? [] : [storageOwner(storageId)])];
      onSaved(await saveDrafts(owners, drafts));
      setDrafts({});
      setStep(3);
    });

  const finish = () =>
    withBusy(async () => {
      if (!selections) return;
      await api.saveCapabilities(toList(selections, capabilities));
      await api.saveStorage(storageId);
      onConfigured();
      navigate("/", { replace: true });
    });

  const missing = selections ? missingRequired(selections, capabilities) : [];
  const canNext = [choice !== null, missing.length === 0, true, true, true][step];
  const next = () => (step === 2 ? void saveCredentialStep() : step === 4 ? void finish() : setStep(step + 1));

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t("setup.title")}</h1>
        <StepIndicator current={step} />
      </div>
      {step === 0 && <PresetStep presets={presets} value={choice} onChange={pickPreset} />}
      {step === 1 && selections && (
        <CapabilityTable
          capabilities={capabilities}
          providers={providers}
          selections={selections}
          region={choice === "cn" || choice === "global" ? choice : null}
          onChange={setSelections}
        />
      )}
      {step === 2 && (
        <CredentialStep
          providers={inUse}
          storage={data.storage}
          storageId={storageId}
          onStorageChange={setStorageId}
          credentials={data.credentials}
          drafts={drafts}
          onDraftChange={(owner, draft) => setDrafts((prev) => ({ ...prev, [owner]: draft }))}
          showStorage={!isDemo}
        />
      )}
      {step === 3 && <ConnectionStep providerIds={testIds} providers={providers} />}
      {step === 4 && selections && (
        <SummaryStep
          capabilities={capabilities}
          providers={providers}
          selections={selections}
          storage={data.storage.find((s) => s.id === storageId)}
        />
      )}
      {error && <div className={ERROR_BOX}>{error}</div>}
      <div className="flex items-center gap-3">
        {step > 0 && (
          <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={() => setStep(step - 1)}>
            {step === 3 ? t("setup.backToEdit") : t("setup.back")}
          </button>
        )}
        <button type="button" className={`${BTN_PRIMARY} ml-auto`} disabled={!canNext || busy} onClick={next}>
          {busy ? t("setup.saving") : step === STEPS.length - 1 ? t("setup.finish") : t("setup.next")}
        </button>
      </div>
    </div>
  );
}
